""" Experiment-specific parameters. """

__author__ = "Johannes Kaiser"
__email__ = "johannes.kaiser@tum.de"


# This dict contains groups of sweep 
# to conduct parameter sweeps
sweep_config = {
    # No patch mode, vox2cortex
    'method': 'random'
}

metric = {
    'name': 'Val_AverageDistance',
    'goal': 'minimize'
}


parameters_dict = {
    'N_EPOCHS': {
        'value': 30
        },
    'chamfer_loss': {
        'distribution': 'uniform',
        'min': 2,
        'max': 5
        },
    'cosine_loss': {
        'distribution': 'uniform',
        'min': 0.05,
        'max': 0.1
        },
    'laplace_loss': {
        'distribution': 'uniform',
        'min': 0,
        'max': 0.1
        },
    'normal_loss': {
        'distribution': 'uniform',
        'min': 0.14,
        'max': 0.3
        },
    'edge_loss': {
        'distribution': 'uniform',
        'min': 0,
        'max': 45
        },
    'cycle_loss': {
        'distribution': 'uniform',
        'min': 0,
        'max': 5
        },
    'avg_edge_loss': {
        'distribution': 'uniform',
        'min': 200,
        'max': 1000
        },
    'pca_loss': {
        'distribution': 'uniform',
        'min': 0,
        'max': 0.05
        },
    }

command = {


}


def get_sweep_config():
    config = sweep_config
    config['metric'] = metric
    config['parameters'] = parameters_dict
    return config

def update_hps_sweep(hps, config):
    hps['N_EPOCHS'] = config.N_EPOCHS
    hps['MESH_LOSS_FUNC_WEIGHTS'] = [
            [config.chamfer_loss] * 4, # Chamfer
            [config.cosine_loss] * 4, # Cosine,
            [config.laplace_loss] * 4, # Laplace,
            [config.normal_loss] * 4, # NormalConsistency
            [config.edge_loss] * 4, # Edge
            [config.cycle_loss] * 4, # Cycle
            [config.avg_edge_loss] * 4, # AvgEdge
            [config.pca_loss] * 4 # PCA
        ]
    return hps