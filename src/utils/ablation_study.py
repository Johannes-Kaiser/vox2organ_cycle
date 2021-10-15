
""" Set parameters for ablation study. """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

from utils.losses import ChamferAndNormalsLoss

AVAILABLE_ABLATIONS = (
    "voxel2mesh architecture",
    "standard chamfer loss",
    "no inter-mesh exchange",
    "elliptic template",
    "laplace on coordinates"
)

def _elliptic_template_params(hps):
    """ Update params for ablation study 'elliptic template' """
    hps['N_TEMPLATE_VERTICES'] = 40962
    hps['MODEL_CONFIG']['MESH_TEMPLATE'] =\
            f"../supplementary_material/white_pial/cortex_4_ellipsoid_{hps['N_TEMPLATE_VERTICES']}_sps{hps['SELECT_PATCH_SIZE']}_ps{hps['PATCH_SIZE']}.obj"

def _standard_chamfer_loss_params(hps):
    """ Update params for ablation study 'standard chamfer loss' """
    for lf in hps['MESH_LOSS_FUNC']:
        if isinstance(lf, ChamferAndNormalsLoss):
            lf.curv_weight_max = 1.0

def _voxel2mesh_architecture_params(hps):
    """ Update params to match the voxel2mesh architecture. """
    # Only sample from decoder
    hps['MODEL_CONFIG']['AGGREGATE_INDICES'] = [[5], [6], [7], [8]]
    hps['MODEL_CONFIG']['AGGREGATE'] = 'lns'

def set_ablation_params_(hps: dict, ablation_study_id: str):
    """ Update the parameters of 'hps' such that they fit the respective
    ablation study. """

    # Check if study is possible
    if ablation_study_id not in AVAILABLE_ABLATIONS:
        raise ValueError(
            f"Ablation study '{ablation_study_id}' not available,"
            f" possible values are {AVAILABLE_ABLATIONS}"
        )

    # Overwrite params
    if ablation_study_id == 'elliptic template':
        _elliptic_template_params(hps)

    elif ablation_study_id == 'standard chamfer loss':
        _standard_chamfer_loss_params(hps)

    elif ablation_study_id == 'voxel2mesh architecture':
        _voxel2mesh_architecture_params(hps)

    else:
        raise NotImplementedError()
