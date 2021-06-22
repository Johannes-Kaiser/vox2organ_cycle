""" UNet architecture """

import torch
import torch.nn as nn
import torch.nn.functional as F
from IPython import embed

from utils.utils_voxel2meshplusplus.custom_layers import IdLayer

class UNetLayer(nn.Module):
    """ U-Net Layer
    Implementation taken from https://github.com/cvlab-epfl/voxel2mesh
    """
    def __init__(self, num_channels_in, num_channels_out, ndims):

        super(UNetLayer, self).__init__()

        conv_op = nn.Conv2d if ndims == 2 else nn.Conv3d
        batch_nrom_op = nn.BatchNorm2d if ndims == 2 else nn.BatchNorm3d

        conv1 = conv_op(num_channels_in,  num_channels_out, kernel_size=3, padding=1)
        conv2 = conv_op(num_channels_out, num_channels_out, kernel_size=3, padding=1)

        bn1 = batch_nrom_op(num_channels_out)
        bn2 = batch_nrom_op(num_channels_out)
        self.unet_layer = nn.Sequential(conv1, bn1, nn.ReLU(), conv2, bn2, nn.ReLU())

    def forward(self, x):
        return self.unet_layer(x)

class ResidualBlock(nn.Module):
    """ Residual Block of https://arxiv.org/abs/1908.02182,
    implementation at https://github.com/MIC-DKFZ/nnUNet
    """

    def __init__(self, num_channels_in, num_channels_out,
                 norm=nn.BatchNorm3d, p_dropout=None):

        super().__init__()
        self.conv1 = nn.Conv3d(num_channels_in, num_channels_out,
                               kernel_size=3, padding=1)
        self.norm1 = norm(num_channels_out)
        if p_dropout is not None:
            self.dropout = nn.Dropout(p_dropout)
        else:
            self.dropout = IdLayer()

        self.conv2 = nn.Conv3d(num_channels_out, num_channels_out, kernel_size=3,
                          padding=1)
        self.norm2 = norm(num_channels_out)

        # 1x1x1 conv to adapt channels of residual
        if num_channels_in != num_channels_out:
            self.adapt_skip = nn.Sequential(nn.Conv3d(num_channels_in,
                                                      num_channels_out, 1,
                                                      bias=False),
                                            norm(num_channels_out))
        else:
            self.adapt_skip = IdLayer()

    def forward(self, x):
        x_out = self.dropout(self.conv1(x))
        x_out = F.relu(self.norm1(x_out))

        x_out = self.norm2(self.conv2(x_out))

        res = self.adapt_skip(x)

        x_out += res

        return F.relu(x_out)

class ResidualUNetEncoder(nn.Module):
    """ Residual UNet encoder oriented on https://github.com/MIC-DKFZ/nnUNet.

    :param input_channels: The number of image channels
    :param encoder_channels: List of channel dimensions of all feature maps
    :returns: Encoded feature maps for every encoder step
    """
    def __init__(self, input_channels: int, encoder_channels):
        super().__init__()

        self.num_steps = len(encoder_channels)
        self.channels = encoder_channels

        # Initial step: Conv --> Residual block
        down_layers = [nn.Sequential(
            nn.Conv3d(input_channels, self.channels[0], 3, padding=1),
            ResidualBlock(self.channels[0], self.channels[0])
        )]
        for i in range(1, self.num_steps):
            # Downsample --> Residual Block
            down_layers.append(nn.Sequential(
                # Compared to Kong et al. we use 2x2x2 convs (instead of 3x3x3)
                # for downsampling
                nn.Conv3d(self.channels[i-1], self.channels[i], 2, stride=2),
                ResidualBlock(self.channels[i], self.channels[i])
            ))

        self.encoder = nn.ModuleList(down_layers)

    def forward(self, x):
        skips = []

        for layer in self.encoder:
            x = layer(x)
            skips.append(x)

        return skips

class ResidualUNetDecoder(nn.Module):
    """ Residual UNet decoder oriented on https://github.com/MIC-DKFZ/nnUNet.

    :param encoder: The encoder from which the decoder receives features
    :param decoder_channels: List of channel dimensions of all feature maps
    :param num_classes: The number of classes to segment
    :returns: Segmentation output
    """
    def __init__(self, encoder, decoder_channels, num_classes, patch_shape,
                 deep_supervision):
        super().__init__()
        # Decoder has one step less
        num_steps = encoder.num_steps - 1
        self.num_classes = num_classes
        self.channels = decoder_channels
        self.deep_supervision = deep_supervision
        self.deep_supervision_pos = (1, 2)
        deep_supervision_layers = []

        up_layers = []
        # First decoder step: Upsample --> Residual Blocks
        up_layers.append(nn.Sequential(
            nn.ConvTranspose3d(
                encoder.channels[-1],
                self.channels[0],
                kernel_size=2,
                stride=2
            ),
            ResidualBlock(
                encoder.channels[-2] + self.channels[0], self.channels[0]
            )
        ))

        # Decoder steps: Upsample --> Residual Blocks
        for i in range(1, num_steps):
            up_layers.append(nn.Sequential(
                nn.ConvTranspose3d(
                    self.channels[i-1],
                    self.channels[i],
                    kernel_size=2,
                    stride=2
                ),
                ResidualBlock(
                    encoder.channels[-i-2] + self.channels[i],
                    self.channels[i]
                )
            ))
            if deep_supervision and i in self.deep_supervision_pos:
                deep_supervision_layers.append(nn.Sequential(
                    nn.Upsample(patch_shape, mode='trilinear',
                                align_corners=False),
                    nn.Conv3d(self.channels[i], num_classes, 1, bias=False)
                ))

        self.deep_supervision_layers = nn.ModuleList(
            deep_supervision_layers
        )

        # Segmenation layer
        self.final_layer = nn.Conv3d(self.channels[-1], num_classes, 1,
                                     bias=False)

        self.decoder = nn.ModuleList(up_layers)

    def forward(self, skips):
        # Reverse order of skips from encoder
        down_skips = skips[::-1]

        x = down_skips[0]

        up_skips = []
        seg = []
        i_ds = 0 # For counting deep supervisions

        for i, layer in enumerate(self.decoder):
            # Upsample
            x = layer[0](x)
            x = torch.cat((x, down_skips[i+1]), dim=1)
            # Residual block
            x = layer[1](x)
            up_skips.append(x)

            if i in self.deep_supervision_pos:
                seg.append(self.deep_supervision_layers[i_ds](x))
                i_ds += 1

        seg.append(self.final_layer(x))

        return up_skips, seg


class ResidualUNet(nn.Module):
    """ Residual UNet oriented on https://github.com/MIC-DKFZ/nnUNet.
    It allows to flexibly exchange the size of the decoder and to get feature
    maps from different stages of the encoder and/or decoder.
    """
    def __init__(self, num_classes: int, num_input_channels: int, patch_shape,
                 down_channels, up_channels, deep_supervision,
                 voxel_decoder: bool):
        assert len(up_channels) == len(down_channels) - 1,\
                "Encoder should have one more step than decoder."
        super().__init__()
        self.num_classes = num_classes

        self.encoder = ResidualUNetEncoder(num_input_channels, down_channels)
        if voxel_decoder:
            self.decoder = ResidualUNetDecoder(self.encoder, up_channels,
                                               num_classes, patch_shape,
                                               deep_supervision)
        else:
            self.decoder = None

    def forward(self, x):
        encoder_skips = self.encoder(x)
        if self.decoder is not None:
            decoder_skips, seg_out = self.decoder(encoder_skips)
        else:
            decoder_skips, seg_out = None, None

        return encoder_skips, decoder_skips, seg_out
