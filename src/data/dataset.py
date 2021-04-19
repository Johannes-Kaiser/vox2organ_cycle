
""" Making datasets accessible

The file contains one base class for all datasets and a separate subclass for
each used dataset.
"""

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

import os
import random
from enum import IntEnum

import numpy as np
import torch.utils.data
import torch.nn.functional as F
import nibabel as nib

import trimesh

from utils.modes import DataModes
from utils.utils import create_mesh_from_voxels

class SupportedDatasets(IntEnum):
    """ List supported datasets """
    Hippocampus = 1

def _box_in_bounds(box, image_shape):
    """ From https://github.com/cvlab-epfl/voxel2mesh """
    newbox = []
    pad_width = []

    for box_i, shape_i in zip(box, image_shape):
        pad_width_i = (max(0, -box_i[0]), max(0, box_i[1] - shape_i))
        newbox_i = (max(0, box_i[0]), min(shape_i, box_i[1]))

        newbox.append(newbox_i)
        pad_width.append(pad_width_i)

    needs_padding = any(i != (0, 0) for i in pad_width)

    return newbox, pad_width, needs_padding

def crop_indices(image_shape, patch_shape, center):
    """ From https://github.com/cvlab-epfl/voxel2mesh """
    box = [(i - ps // 2, i - ps // 2 + ps) for i, ps in zip(center, patch_shape)]
    box, pad_width, needs_padding = _box_in_bounds(box, image_shape)
    slices = tuple(slice(i[0], i[1]) for i in box)
    return slices, pad_width, needs_padding


def crop(image, patch_shape, center, mode='constant'):
    """ From https://github.com/cvlab-epfl/voxel2mesh """
    slices, pad_width, needs_padding = crop_indices(image.shape, patch_shape, center)
    patch = image[slices]

    if needs_padding and mode != 'nopadding':
        if isinstance(image, np.ndarray):
            if len(pad_width) < patch.ndim:
                pad_width.append((0, 0))
            patch = np.pad(patch, pad_width, mode=mode)
        elif isinstance(image, torch.Tensor):
            assert len(pad_width) == patch.dim(), "not supported"
            # [int(element) for element in np.flip(np.array(pad_width).flatten())]
            patch = F.pad(patch, tuple([int(element) for element in np.flip(np.array(pad_width), axis=0).flatten()]), mode=mode)

    return patch

def img_with_patch_size(img: np.ndarray, patch_size: int, is_label: bool) -> torch.tensor:
    """ Pad/interpolate an image such that it has a certain shape
    """
    # TODO: Not sure about the effect of this procedure --> analyse

    D, H, W = img.shape
    center_z, center_y, center_x = D // 2, H // 2, W // 2
    D, H, W = patch_size
    img = crop(img, (D, H, W), (center_z, center_y, center_x))

    img = torch.from_numpy(img).float()

    if is_label:
        img = F.interpolate(img[None, None].float(), patch_size, mode='nearest')[0, 0].long()
    else:
        img = F.interpolate(img[None, None], patch_size, mode='trilinear', align_corners=False)[0, 0]

    return img

class DatasetHandler(torch.utils.data.Dataset):
    """
    Base class for all datasets. It implements a map-style dataset, see
    https://pytorch.org/docs/stable/data.html.

    :param list ids: The ids of the files the dataset split should contain
    :param DataModes datamode: TRAIN, VALIDATION, or TEST

    """

    def __init__(self, ids: list, mode: DataModes):
        self._mode = mode
        self._files = ids

    def __getitem__(self, key):
        if isinstance(key, slice):
            # get the start, stop, and step from the slice
            return [self[ii] for ii in range(*key.indices(len(self)))]
        if isinstance(key, int):
            # handle negative indices
            if key < 0:
                key += len(self)
            if key < 0 or key >= len(self):
                raise IndexError(f"The index {key} is out of range.")
            # get the data from direct index
            return self.get_item_from_index(key)

        raise TypeError("Invalid argument type.")

    def get_item_from_index(self, index: int):
        """
        For training and validation datasets, an item consists of (data, labels)
        while test datasets only contain (data)

        :param int index: The index of the data to access.
        """
        raise NotImplementedError

    def __len__(self):
        raise NotImplementedError

class Hippocampus(DatasetHandler):
    """ Hippocampus dataset from
    https://drive.google.com/file/d/1RzPB1_bqzQhlWvU-YGvZzhx2omcDh38C/view

    It loads all data specified by 'ids' directly into memory. Only ids in
    'imagesTr' are considered (for 'imagesTs' no labels exist).

    :param list ids: The ids of the files the dataset split should contain, example:
        ['hippocampus_101', 'hippocampus_212',...]
    :param DataModes datamode: TRAIN, VALIDATION, or TEST
    :param str raw_data_dir: The raw base folder, contains e.g. subfolders
    imagesTr/ and labelsTr/
    :param str preprocessed_data_dir: Pre-processed data, e.g. meshes created with
    marching cubes.
    :param patch_size: The patch size of the images, e.g. (64, 64, 64)
    :param bool load_mesh: Load a precomputed mesh. If False, meshes are
    calculated with marching cubes.
    :param mc_step_size: The step size for marching cubes generation, only
    relevant if load_mesh is False.
    """

    def __init__(self, ids: list, mode: DataModes, raw_data_dir: str,
                 preprocessed_data_dir: str, patch_size, load_mesh=False,
                 mc_step_size=1):
        super().__init__(ids, mode)

        self._raw_data_dir = raw_data_dir
        self._preprocessed_data_dir = preprocessed_data_dir
        self.patch_size = patch_size

        self.data = self._load_data3D(folder="imagesTr",
                                      patch_size=patch_size,
                                      is_label=False)
        self.voxel_labels = self._load_data3D(folder="labelsTr",
                                              patch_size=patch_size,
                                              is_label=True)
        # Ignore distinction between anterior and posterior hippocampus
        for vl in self.voxel_labels:
            vl[vl > 1] = 1

        if load_mesh:
            self.mesh_labels = self._load_dataMesh(folder="meshlabelsTr")
        else:
            self.mesh_labels = self._calc_dataMesh(mc_step_size)

        assert self.__len__() == len(self.data)
        assert self.__len__() == len(self.voxel_labels)
        assert self.__len__() == len(self.mesh_labels)

    @staticmethod
    def split(raw_data_dir, preprocessed_data_dir, dataset_seed,
              dataset_split_proportions, patch_size, **kwargs):
        """ Create train, validation, and test split of the Hippocampus data"

        :param str raw_data_dir: The raw base folder, contains e.g. subfolders
        imagesTr/ and labelsTr/
        :param str preprocessed_data_dir: Pre-processed data, e.g. meshes created with
        marching cubes.
        :param dataset_seed: A seed for the random splitting of the dataset.
        :param dataset_split_proportions: The proportions of the dataset
        splits, e.g. (80, 10, 10)
        :patch_size: The patch size of the 3D images.
        :return: (Train dataset, Validation dataset, Test dataset)
        """

        # Available files
        all_files = os.listdir(os.path.join(raw_data_dir, "imagesTr"))
        all_files = [fn for fn in all_files if "._" not in fn] # Remove invalid
        all_files = [fn.split(".")[0] for fn in all_files] # Remove file ext.

        # Shuffle with seed
        random.Random(dataset_seed).shuffle(all_files)

        # Split
        assert np.sum(dataset_split_proportions) == 100, "Splits need to sum to 100."
        indices_train = slice(0, dataset_split_proportions[0] * len(all_files) // 100)
        indices_val = slice(indices_train.stop,
                            indices_train.stop +\
                                (dataset_split_proportions[1] * len(all_files) // 100))
        indices_test = slice(indices_val.stop, len(all_files))

        # Create datasets
        train_dataset = Hippocampus(all_files[indices_train],
                                    DataModes.TRAIN,
                                    raw_data_dir,
                                    preprocessed_data_dir,
                                    patch_size)
        val_dataset = Hippocampus(all_files[indices_val],
                                  DataModes.VALIDATION,
                                  raw_data_dir,
                                  preprocessed_data_dir,
                                  patch_size)
        test_dataset = Hippocampus(all_files[indices_test],
                                  DataModes.TEST,
                                  raw_data_dir,
                                  preprocessed_data_dir,
                                  patch_size)

        return train_dataset, val_dataset, test_dataset

    def __len__(self):
        return len(self._files)

    def get_item_from_index(self, index: int):
        """
        One data item has the form
        (data, 3D voxel label, mesh label)
        with types
        (torch.tensor, torch.tensor, trimesh.base.Trimesh)
        """
        return self.data[index],\
                self.voxel_labels[index],\
                self.mesh_labels[index]

    def _load_data3D(self, folder: str, patch_size, is_label: bool):
        data_dir = os.path.join(self._raw_data_dir, folder)
        data = []
        for fn in self._files:
            d = nib.load(os.path.join(data_dir, fn + ".nii.gz")).get_fdata()
            if is_label:
                d = img_with_patch_size(d, patch_size, True)
            else:
                d = img_with_patch_size(d, patch_size, False)
            data.append(d)

        return data

    def _calc_dataMesh(self, mc_step_size):
        meshes = []
        for v in self.voxel_labels:
            meshes.append(create_mesh_from_voxels(v, mc_step_size))

        return meshes

    def _load_dataMesh(self, folder):
        data_dir = os.path.join(self._preprocessed_data_dir, folder)
        data = []
        for fn in self._files:
            d = trimesh.load_mesh(os.path.join(data_dir, fn + ".ply"))
            data.append(d)

        return data

# Mapping supported datasets to split functions
dataset_split_handler = {
    SupportedDatasets.Hippocampus.name: Hippocampus.split
}
