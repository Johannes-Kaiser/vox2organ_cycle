
""" Experiment-specific parameters. """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

from utils.losses import (
    ChamferLoss,
    ChamferAndNormalsLoss,
    LaplacianLoss,
    NormalConsistencyLoss,
    EdgeLoss
)
from utils.utils_voxel2meshplusplus.graph_conv import (
    GraphConvNorm,
)

# This dict contains groups of parameters that kind of belong together in order
# to conduct certain experiments
hyper_ps_groups = {
    # No patch mode, vox2cortex
    'default': {
        'MESH_LOSS_FUNC': [
           ChamferAndNormalsLoss(curv_weight_max=5.0),
           LaplacianLoss(),
           NormalConsistencyLoss(),
           EdgeLoss(0.0)
        ],
        'PATCH_MODE': 'no',
        # Order of structures: lh_white, rh_white, lh_pial, rh_pial; mesh loss
        # weights should respect this order!
        'MESH_LOSS_FUNC_WEIGHTS': [
            [4.0] * 4, # Chamfer
            [0.01] * 2 + [0.0125] * 2, # Cosine,
            [0.1] * 2 + [0.25] * 2, # Laplace,
            [0.001] * 2 + [0.00225] * 2, # NormalConsistency
            [5.0] * 4 # Edge
        ],
        'N_M_CLASSES': 4,
        'PATCH_SIZE': [128, 144, 128],
        'SELECT_PATCH_SIZE': [192, 208, 192],
        'MODEL_CONFIG': {
            'GRAPH_CHANNELS': [256, 64, 64, 64, 64],
            'UNPOOL_INDICES': [0,0,0,0],
            'AGGREGATE_INDICES': [
                [3,4,5,6],
                [2,3,6,7],
                [1,2,7,8],
                [0,1,7,8] # 8 = last decoder skip
            ],
            'NORM': 'batch', # Only for graph convs
            'DECODER_CHANNELS': [64, 32, 16, 8],
            'DEEP_SUPERVISION': True,
            'WEIGHTED_EDGES': False,
            'PROPAGATE_COORDS': True,
            'VOXEL_DECODER': True,
            'GC': GraphConvNorm,
            'GROUP_STRUCTS': [[0, 1], [2, 3]],
        }
    },
    # One hemisphere, vox2cortex
    'one hemisphere': {
        'MESH_LOSS_FUNC': [
           ChamferAndNormalsLoss(curv_weight_max=5.0),
           LaplacianLoss(),
           NormalConsistencyLoss(),
           EdgeLoss(0.0)
        ],
        'PATCH_MODE': 'single-patch',
        # Order of structures: lh_white, rh_white, lh_pial, rh_pial; mesh loss
        # weights should respect this order!
        'MESH_LOSS_FUNC_WEIGHTS': [
            [4.0] * 2, # Chamfer
            [0.01] + [0.0125], # Cosine,
            [0.1] + [0.25], # Laplace,
            [0.001] + [0.00225], # NormalConsistency
            [5.0] * 2 # Edge
        ],
        'N_M_CLASSES': 2,
        'PATCH_SIZE': [64, 144, 128],
        'SELECT_PATCH_SIZE': [96, 208, 192],
        'MODEL_CONFIG': {
            'GRAPH_CHANNELS': [256, 64, 64, 64, 64],
            'UNPOOL_INDICES': [0,0,0,0],
            'AGGREGATE_INDICES': [
                [3,4,5,6],
                [2,3,6,7],
                [1,2,7,8],
                [0,1,7,8] # 8 = last decoder skip
            ],
            'NORM': 'batch', # Only for graph convs
            'DECODER_CHANNELS': [64, 32, 16, 8],
            'DEEP_SUPERVISION': True,
            'WEIGHTED_EDGES': False,
            'PROPAGATE_COORDS': True,
            'VOXEL_DECODER': True,
            'GC': GraphConvNorm,
            'GROUP_STRUCTS': [[0], [1]],
        }
    },

    # Experiments with cortical flow architecture
    'Cortical Flow single-patch': {
        'ARCHITECTURE': 'corticalflow',
        'FREEZE_PRE_TRAINED': True,
        'N_TEMPLATE_VERTICES': 41602,
        'MODEL_CONFIG': {
            # Cortical flow model
            # 'UNPOOL_INDICES': [1,1,1],
            'ENCODER_CHANNELS': [
                [16, 32, 64, 128, 256],
                [16, 32, 64],
                [16, 32, 64]
            ],
            'DECODER_CHANNELS': [
                [128, 64, 32, 16],
                [32, 16],
                [32, 16]
            ],
        },
        'PATCH_SIZE': [96, 208, 192],
        'SELECT_PATCH_SIZE': [96, 208, 192],
        'MESH_LOSS_FUNC': [
           ChamferLoss(),
           EdgeLoss(0.0)
        ],
        'PATCH_MODE': 'single-patch',
        # Order of structures: rh_white, rh_pial
        'MESH_LOSS_FUNC_WEIGHTS': [
            [1.0] * 2, # Chamfer
            [1.0] * 2 # Edge
        ],
        # No voxel decoder --> set voxel loss weights to 0
        'VOXEL_LOSS_FUNC_WEIGHTS': [],
        'VOXEL_LOSS_FUNC': [],
        'EVAL_METRICS': [
            'SymmetricHausdorff',
            'JaccardMesh',
            'Chamfer',
            'CorticalThicknessError',
            'AverageDistance'
        ],
        'OPTIM_PARAMS': {
            'graph_lr': None,
        },
    }
}
