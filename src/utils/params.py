""" Documentation of project-wide parameters and default values 

Ideally, all occurring parameters should be documented here.
"""

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

from enum import Enum

import torch

from utils.losses import (
    ChamferLoss,
    LaplacianLoss,
    NormalConsistencyLoss,
    EdgeLoss
)
from utils.utils_voxel2meshplusplus.graph_conv import (
    GraphConvNorm
)

hyper_ps_default={

    # >>> Note: Using tuples (...) instead of lists [...] may lead to problems
    # when resuming broken trainings (json converts tuples to lists when dumping).
    # Therefore, it is recommended to use lists for parameters here.

    # The number of vertex classes to distinguish (including background)
    'N_V_CLASSES': 2,

    # The number of mesh classes. This is usually the number of non-connected
    # components/structures
    'N_M_CLASSES': 2,

    # The number of vertices in a single template structure
    'N_TEMPLATE_VERTICES': 162,

    # The number of reference points in a cortex structure
    'N_REF_POINTS_PER_STRUCTURE': 40962,

    # Either use a mesh or a pointcloud as ground truth. Basically, if one
    # wants to compute only point losses like the Chamfer loss, a pointcloud is
    # sufficient while other losses like cosine distance between vertex normals
    # require a mesh (pointcloud + faces)
    'MESH_TARGET_TYPE': "pointcloud",

    # The type of meshes used, either 'freesurfer' or 'marching cubes'
    'MESH_TYPE': 'marching cubes',

    # The mode for reduction of mesh regularization losses, either 'linear' or
    # 'none'
    'REDUCE_REG_LOSS_MODE': 'none',

    # The structure type for cortex data, either 'cerebral_cortex' or
    # 'white_matter'
    'STRUCTURE_TYPE': "white_matter",

    # The batch size used during training
    'BATCH_SIZE': 1,

    # Activate/deactivate patch mode for the cortex dataset. Possible values
    # are "no", "single-patch", "multi-patch"
    'PATCH_MODE': "no",

    # Accumulate n gradients before doing a backward pass
    'ACCUMULATE_N_GRADIENTS': 1,

    # The number of training epochs
    'N_EPOCHS': 5,

    # Freesurfer ground truth meshes with reduced resolution. 1.0 = original
    # resolution (in terms of number of vertices)
    'REDUCED_FREESURFER': 1.0,

    # Whether to use curvatures of the meshes. If set to True, the ground truth
    # points are vertices and not sampled surface points
    'PROVIDE_CURVATURES': False,

    # The optimizer used for training
    'OPTIMIZER_CLASS': torch.optim.Adam,

    # Parameters for the optimizer. A separate learning rate for the graph
    # network can be specified
    'OPTIM_PARAMS': {'lr': 1e-4, 'graph_lr': None},

    # Data augmentation
    'AUGMENT_TRAIN': False,

    # Whether or not to use Pytorch's automatic mixed precision
    'MIXED_PRECISION': False,

    # The used loss functions for the voxel segmentation
    'VOXEL_LOSS_FUNC': [torch.nn.CrossEntropyLoss()],

    # The weights for the voxel loss functions
    'VOXEL_LOSS_FUNC_WEIGHTS': [1.],

    # The used loss functions for the mesh
    'MESH_LOSS_FUNC': [ChamferLoss(),
                       LaplacianLoss(),
                       NormalConsistencyLoss(),
                       EdgeLoss(0.0)],

    # The weights for the mesh loss functions, given are the values from
    # Wickramasinghe et al. Kong et al. used a geometric averaging and weights
    # [0.3, 0.05, 0.46, 0.16]
    'MESH_LOSS_FUNC_WEIGHTS': [1.0, 0.1, 0.1, 1.0],

    # The number of sample points for the mesh loss computation if done as by
    # Wickramasinghe 2020, i.e. sampling n random points from the outer surface
    # of the voxel ground truth
    'N_SAMPLE_POINTS': 3000,

    # The way the weighted average of the losses is computed,
    # e.g. 'linear' weighted average, 'geometric' mean
    'LOSS_AVERAGING': 'linear',

    # Log losses etc. every n iterations or 'epoch'
    'LOG_EVERY': 1,

    # Evaluate model every n epochs
    'EVAL_EVERY': 1,

    # Use early stopping
    'EARLY_STOP': False,

    # The metrics used for evaluation, see utils.evaluate.EvalMetrics for
    # options
    'EVAL_METRICS': [
        'Wasserstein',
        'SymmetricHausdorff',
        'JaccardVoxel',
        'JaccardMesh',
        'Chamfer'
    ],

    # Main validation metric according to which the best model is determined.
    # Note: This one must also be part of 'EVAL_METRICS'!
    'MAIN_EVAL_METRIC': 'JaccardMesh',

    # The number of image dimensions. This parameter is deprecated since
    # dimensionality is now inferred from the patch size.
    'NDIMS': 3,

    # Voxel2Mesh original parameters
    # (from https://github.com/cvlab-epfl/voxel2mesh).
    # Note that not for all models/architectures all of
    # those parameters are relevant.
    'MODEL_CONFIG': {
        'FIRST_LAYER_CHANNELS': 16,
        'ENCODER_CHANNELS': [16, 32, 64, 128, 256],
        'DECODER_CHANNELS': [128, 64, 32, 16], # Voxel decoder
        'GRAPH_CHANNELS': [32, 32, 32, 32, 32], # Graph decoder
        'NUM_INPUT_CHANNELS': 1,
        'STEPS': 4,
        'DEEP_SUPERVISION': False, # For voxel net
        'NORM': 'none', # Only for graph convs, batch norm always used in voxel layers
        # Number of hidden layers in the graph conv blocks
        'GRAPH_CONV_LAYER_COUNT': 4,
        'MESH_TEMPLATE': '../supplementary_material/spheres/icosahedron_162.obj',
        'UNPOOL_INDICES': [0,1,0,1,0],
        'USE_ADOPTIVE_UNPOOL': False,
        # Weighted feature aggregation in graph convs (only possible with
        # pytorch-geometric graph convs)
        'WEIGHTED_EDGES': False,
        # Whether to use a voxel decoder
        'VOXEL_DECODER': True,
        # The graph conv implementation to use
        'GC': GraphConvNorm,
        # Whether to propagate coordinates in the graph decoder in addition to
        # voxel features
        'PROPAGATE_COORDS': False,
        # Dropout probability of UNet blocks
        'P_DROPOUT': None,
        # The used patch size, should be equal to global patch size
        'PATCH_SIZE': [64, 64, 64],
        # Where to take the features from the UNet
        'AGGREGATE_INDICES': [[5,6],[6,7],[7,8]]
    },

    # Decay the learning rate by multiplication with 'LR_DECAY_RATE' if no
    # improvement for 'LR_DECAY_AFTER' epochs
    'LR_DECAY_RATE': 0.5,
    'LR_DECAY_AFTER': -1, # -1 = no decay

    # input should be cubic. Otherwise, input should be padded accordingly.
    'PATCH_SIZE': [64, 64, 64],

    # For selecting a patch from cortex dataset.
    'SELECT_PATCH_SIZE': [192, 224, 192],

    # Seed for dataset splitting
    'DATASET_SEED': 1234,

    # Proportions of dataset splits
    'DATASET_SPLIT_PROPORTIONS': [80, 10, 10],

    # The directory where experiments are stored
    'EXPERIMENT_BASE_DIR': "../experiments/",

    # Directory of raw data
    'RAW_DATA_DIR': "/raw/data/dir", # <<<< Needs to set (e.g. in main.py)

    # Directory of preprocessed data
    'PREPROCESSED_DATA_DIR': "/preprocessed/data/dir", # <<<< Needs to set (e.g. in main.py)
}
