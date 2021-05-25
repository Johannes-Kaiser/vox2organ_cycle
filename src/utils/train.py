
""" Training procedure """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

import os
import logging
from copy import deepcopy

import json
import torch
import numpy as np
from torch.utils.data import DataLoader
from torch.cuda.amp import GradScaler, autocast
from torch.optim.lr_scheduler import ReduceLROnPlateau
from pytorch3d.structures import Pointclouds

from utils.utils import string_dict, score_is_better
from utils.logging import (
    init_logging,
    log_losses,
    log_epoch,
    log_lr,
    get_log_dir,
    measure_time,
    write_img_if_debug,
    log_deltaV,
    log_model_tensorboard_if_debug,
    log_val_results)
from utils.modes import ExecModes
from utils.evaluate import ModelEvaluator
from utils.losses import (
    all_linear_loss_combine,
    voxel_linear_mesh_geometric_loss_combine)
from data.supported_datasets import dataset_split_handler
from models.model_handler import ModelHandler

# Model names
INTERMEDIATE_MODEL_NAME = "intermediate.model"
BEST_MODEL_NAME = "best.model"
FINAL_MODEL_NAME = "final.model"

class Solver():
    """
    Solver class for optimizing the weights of neural networks.

    :param int n_classes: The number of classes to distinguish (including
    background)
    :param torch.optim optimizer_class: The optimizer to use, e.g. Adam.
    :param dict optim_params: The parameters for the optimizer. If empty,
    default values are used.
    :param evaluator: Evaluator for the optimized model.
    :param list voxel_loss_func: A list of loss functions to apply for the 3D voxel
    prediction.
    :param list voxel_loss_func_weights: A list of the same length of 'voxel_loss_func'
    with weights for the losses.
    :param list mesh_loss_func: A list of loss functions to apply for the mesh
    prediction.
    :param list mesh_loss_func_weights: A list of the same length of 'mesh_loss_func'
    with weights for the losses.
    :param str loss_averaging: The way the weighted average of the losses is
    computed, e.g. 'linear' weighted average, 'geometric' mean
    :param str save_path: The path where results and stats are saved.
    :param log_every: Log the stats every n iterations.
    :param n_sample_points: The number of points sampled for mesh loss
    computation.
    :param str device: The device for execution, e.g. 'cuda:0'.
    :param str main_eval_metric: The main evaluation metric according to which
    the best model is determined.
    :param int accumulate_n_gradients: Gradient accumulation of n gradients.
    :param bool mixed_precision: Whether or not to use automatic mixed
    precision.
    :param int lr_decay_after: see lr_decay_rate
    :param float lr_decay_rate: If no improvement for lr_decay_after epochs,
    then new_lr = old_lr * lr_decay_rate

    """

    def __init__(self,
                 n_classes,
                 optimizer_class,
                 optim_params,
                 evaluator,
                 voxel_loss_func,
                 voxel_loss_func_weights,
                 mesh_loss_func,
                 mesh_loss_func_weights,
                 loss_averaging,
                 save_path,
                 log_every,
                 n_sample_points,
                 device,
                 main_eval_metric,
                 accumulate_n_gradients,
                 mixed_precision,
                 lr_decay_rate,
                 lr_decay_after,
                 **kwargs):

        self.n_classes = n_classes
        self.optim_class = optimizer_class
        self.optim_params = optim_params
        self.optim = None # defined for each training separately
        self.scaler = GradScaler() # for mixed precision
        self.evaluator = evaluator
        self.voxel_loss_func = voxel_loss_func
        self.voxel_loss_func_weights = voxel_loss_func_weights
        assert len(voxel_loss_func) == len(voxel_loss_func_weights),\
                "Number of weights must be equal to number of 3D seg. losses."

        self.mesh_loss_func = mesh_loss_func
        self.mesh_loss_func_weights = mesh_loss_func_weights
        assert len(mesh_loss_func) == len(mesh_loss_func_weights),\
                "Number of weights must be equal to number of mesh losses."

        self.loss_averaging = loss_averaging
        self.save_path = save_path
        self.log_every = log_every
        self.n_sample_points = n_sample_points
        self.device = device
        self.main_eval_metric = main_eval_metric
        self.accumulate_ngrad = accumulate_n_gradients
        self.mixed_precision = mixed_precision
        self.lr_decay_after = lr_decay_after
        self.lr_decay_rate = lr_decay_rate

    @measure_time
    def training_step(self, model, data, iteration):
        """ One training step.

        :param model: Current pytorch model.
        :param data: The minibatch.
        :param iteration: The training iteration (used for logging)
        :returns: The overall (weighted) loss.
        """
        loss_total = self.compute_loss(model, data, iteration)

        if self.mixed_precision:
            self.scaler.scale(loss_total).backward()
        else:
            loss_total.backward()

        # Accumulate gradients
        if iteration % self.accumulate_ngrad == 0:
            if self.mixed_precision:
                self.scaler.step(self.optim)
                self.scaler.update()
            else:
                self.optim.step()

            self.optim.zero_grad()
            logging.getLogger(ExecModes.TRAIN.name).debug("Updated parameters.")

        return loss_total

    @measure_time
    def compute_loss(self, model, data, iteration) -> torch.tensor:
        # Chop data
        x, y, points = data
        points = [Pointclouds(p).cuda() for p in points.permute(1,0,2,3)]

        # Predict
        with autocast(self.mixed_precision):
            pred = model(x.cuda())

        # Log
        write_img_if_debug(x.cpu().squeeze().numpy(),
                           "../misc/voxel_input_img_train.nii.gz")
        write_img_if_debug(y.cpu().squeeze().numpy(),
                           "../misc/voxel_target_img_train.nii.gz")
        if model.__class__.pred_to_voxel_pred(pred) is not None:
            write_img_if_debug(model.__class__.pred_to_voxel_pred(pred).cpu().squeeze().numpy(),
                               "../misc/voxel_pred_img_train.nii.gz")
        if iteration % self.log_every == 0:
            try:
                # Mean over steps, classes, and batch
                disps = model.__class__.pred_to_displacements(pred).mean(dim=(0,1,2))
                log_deltaV(disps, iteration)
            except NotImplementedError:
                pass

        losses = {}
        with autocast(self.mixed_precision):
            if self.loss_averaging == 'linear':
                losses, loss_total = all_linear_loss_combine(
                    self.voxel_loss_func,
                    self.voxel_loss_func_weights,
                    model.__class__.pred_to_raw_voxel_pred(pred),
                    y.cuda(),
                    self.mesh_loss_func,
                    self.mesh_loss_func_weights,
                    model.__class__.pred_to_pred_meshes(pred),
                    points)
            elif self.loss_averaging == 'geometric':
                losses, loss_total = voxel_linear_mesh_geometric_loss_combine(
                    self.voxel_loss_func,
                    self.voxel_loss_func_weights,
                    model.__class__.pred_to_raw_voxel_pred(pred),
                    y.cuda(),
                    self.mesh_loss_func,
                    self.mesh_loss_func_weights,
                    model.__class__.pred_to_pred_meshes(pred),
                    points)
            else:
                raise ValueError("Unknown loss averaging.")

            losses['TotalLoss'] = loss_total

        # log
        if iteration % self.log_every == 0:
            log_losses(losses, iteration)

        return loss_total

    def train(self,
              model: torch.nn.Module,
              training_set: torch.utils.data.Dataset,
              n_epochs: int,
              batch_size: int,
              early_stop: bool,
              eval_every: int,
              start_epoch: int,
              save_models: bool=True):
        """
        Training procedure

        :param model: The model to train.
        :param training_set: The training dataset.
        :param validation_set: The validation dataset.
        :param n_epochs: The number of training epochs.
        :param batch_size: The minibatch size.
        :param early_stop: Enable early stopping.
        :param eval_every: Evaluate the model every n epochs.
        :param start_epoch: Start at this epoch with counting, should be 1
        besides previous training is resumed.
        :param save_models: Save the final and best model.
        """

        best_val_score = None
        best_epoch = 0
        best_state = None

        model.float().to(self.device)
        # Cannot log graph due to Meshes objects
        # log_model_tensorboard_if_debug(model,
                                       # training_set[0][0][None,None].cuda())

        trainLogger = logging.getLogger(ExecModes.TRAIN.name)
        trainLogger.info("Training on device %s", self.device)

        # Optimizer and lr scheduling
        self.optim = self.optim_class(model.parameters(), **self.optim_params)
        self.optim.zero_grad()
        _, lr_decay_mode = score_is_better(0, 0, self.main_eval_metric)
        lr_scheduler = ReduceLROnPlateau(self.optim, lr_decay_mode,
                                         self.lr_decay_rate,
                                         self.lr_decay_after)

        training_loader = DataLoader(training_set, batch_size=batch_size,
                                     shuffle=True)
        trainLogger.info("Created training loader of length %d",
                    len(training_loader))

        # Logging every epoch
        log_was_epoch = False
        if self.log_every == 'epoch':
            log_was_epoch = True
            self.log_every = len(training_loader)

        epochs_file = os.path.join(self.save_path, "models_to_epochs.json")
        models_to_epochs = {}

        iteration = (start_epoch - 1) * len(training_loader) + 1

        for epoch in range(start_epoch, n_epochs+1):
            model.train()

            for iter_in_epoch, data in enumerate(training_loader):
                if iteration % self.log_every == 0:
                    trainLogger.info("Iteration: %d", iteration)
                    log_epoch(epoch, iteration)
                    log_lr(np.mean([p['lr'] for p in self.optim.param_groups]),
                           iteration)
                # Step
                loss = self.training_step(model, data, iteration)

                iteration += 1

            # Evaluate
            if epoch % eval_every == 0 or epoch == n_epochs or epoch == 1:
                model.eval()
                val_results = self.evaluator.evaluate(model, epoch,
                                                      save_meshes=5)
                log_val_results(val_results, iteration - 1)

                # Main validation score
                main_val_score = val_results[self.main_eval_metric]
                if score_is_better(best_val_score, main_val_score,
                                   self.main_eval_metric)[0]:
                    best_val_score = main_val_score
                    best_state = deepcopy(model.state_dict())
                    best_epoch = epoch
                    if save_models:
                        model.save(os.path.join(self.save_path, BEST_MODEL_NAME))
                        models_to_epochs[BEST_MODEL_NAME] = best_epoch
            lr_scheduler.step(best_val_score)

            # TODO: Early stopping

            # Save intermediate model after each epoch
            if save_models:
                model.eval()
                model.save(os.path.join(self.save_path, INTERMEDIATE_MODEL_NAME))
                models_to_epochs[INTERMEDIATE_MODEL_NAME] = epoch
                with open(epochs_file, 'w') as f:
                    json.dump(models_to_epochs, f)
                trainLogger.debug("Saved intermediate model from epoch %d.",
                                  epoch)

        # Save final model
        if save_models:
            model.eval()
            model.save(os.path.join(self.save_path, FINAL_MODEL_NAME))
            models_to_epochs[FINAL_MODEL_NAME] = epoch
            if best_state is not None:
                trainLogger.info("Best model in epoch %d", best_epoch)

            # Save epochs corresponding to models
            with open(epochs_file, 'w') as f:
                json.dump(models_to_epochs, f)

            trainLogger.info("Saved models at %s", self.save_path)

            if log_was_epoch:
                self.log_every = 'epoch'

        # Return last main validation score
        return main_val_score

def create_exp_directory(experiment_base_dir, experiment_name):
    """ Create experiment directory and potentially subdirectories for logging
    etc.
    """

    # Define name
    if experiment_name is not None:
        experiment_dir = os.path.join(experiment_base_dir, experiment_name)
    else:
        # Automatically enumerate experiments exp_i
        ids_exist = []
        for n in os.listdir(experiment_base_dir):
            try:
                ids_exist.append(int(n.split("_")[-1]))
            except ValueError:
                pass
        if len(ids_exist) > 0:
            new_id = np.max(ids_exist) + 1
        else:
            new_id = 1

        experiment_name = "exp_" + str(new_id)

        experiment_dir = os.path.join(experiment_base_dir, experiment_name)

    # Create directories
    log_dir = get_log_dir(experiment_dir)
    if experiment_name=="debug":
        # Overwrite
        os.makedirs(log_dir, exist_ok=True)
    else:
        # Throw error if directory exists already
        os.makedirs(log_dir)

    return experiment_name, experiment_dir, log_dir

def training_routine(hps: dict, experiment_name=None, loglevel='INFO',
                     resume=False):
    """
    A full training routine including setup of experiments etc.

    :param dict hps: Hyperparameters to use.
    :param str experiment_name (optional): The name of the experiment
    directory. If None, a name is created automatically.
    :param loglevel: The loglevel of the standard logger to use.
    :param resume: If true, a previous training is resumed.
    :return: The name of the experiment.
    """

    ###### Prepare training experiment ######

    experiment_base_dir = hps['EXPERIMENT_BASE_DIR']

    if not resume:
        # Create directories
        experiment_name, experiment_dir, log_dir =\
                create_exp_directory(experiment_base_dir, experiment_name)
        hps['EXPERIMENT_NAME'] = experiment_name

        # Store hyperparameters
        param_file = os.path.join(experiment_dir, "params.json")
        hps_to_write = string_dict(hps)
        with open(param_file, 'w') as f:
            json.dump(hps_to_write, f)
    else:
        # Directory already exists if training is resumed
        experiment_dir = os.path.join(experiment_base_dir, experiment_name)
        log_dir = get_log_dir(experiment_dir)

        # Read previous config file
        param_file = os.path.join(experiment_dir, "params.json")
        with open(param_file, 'r') as f:
            previous_hps = json.load(f)

        # Check if configs are equal
        hps_to_write = string_dict(hps)
        for k_old, v_old in previous_hps.items():
            if hps_to_write[k_old] != v_old:
                raise RuntimeError(f"Hyperparameter {k_old} is not equal to the"\
                                   " experiment that should be resumed.")

    # Lower case param names as input to constructors/functions
    hps_lower = dict((k.lower(), v) for k, v in hps.items())
    model_config = dict((k.lower(), v) for k, v in hps['MODEL_CONFIG'].items())

    # Configure logging
    init_logging(logger_name=ExecModes.TRAIN.name,
                 exp_name=experiment_name,
                 log_dir=log_dir,
                 loglevel=loglevel,
                 mode=ExecModes.TRAIN,
                 proj_name=hps['PROJ_NAME'],
                 group_name=hps['GROUP_NAME'],
                 params=hps_to_write,
                 time_logging=hps['TIME_LOGGING'])
    trainLogger = logging.getLogger(ExecModes.TRAIN.name)
    trainLogger.info("Start training '%s'...", experiment_name)

    ###### Load data ######
    trainLogger.info("Loading dataset %s...", hps['DATASET'])
    training_set,\
            validation_set,\
            test_set=\
                dataset_split_handler[hps['DATASET']](save_dir=experiment_dir,
                                                      **hps_lower)

    trainLogger.info("%d training files.", len(training_set))
    trainLogger.info("%d validation files.", len(validation_set))
    trainLogger.info("%d test files.", len(test_set))

    ###### Training ######

    model = ModelHandler[hps['ARCHITECTURE']].value(\
                                        ndims=hps['NDIMS'],
                                        num_classes=hps['N_CLASSES'],
                                        patch_shape=hps['PATCH_SIZE'],
                                        **model_config)
    trainLogger.info("%d parameters in the model.", model.count_parameters())
    if resume:
        # Load state and epoch
        model_path = os.path.join(experiment_dir, "intermediate.model")
        trainLogger.info("Loading model %s...", model_path)
        model.load_state_dict(torch.load(model_path))
        epochs_file = os.path.join(experiment_dir, "models_to_epochs.json")
        with open(epochs_file, 'r') as f:
            models_to_epochs = json.load(f)
        start_epoch = models_to_epochs[INTERMEDIATE_MODEL_NAME] + 1
        trainLogger.info("Resuming training from epoch %d", start_epoch)
    else:
        # New training
        start_epoch = 1

    # Evaluation during training on validation set
    evaluator = ModelEvaluator(eval_dataset=validation_set,
                               save_dir=experiment_dir, **hps_lower)

    solver = Solver(evaluator=evaluator, save_path=experiment_dir, **hps_lower)

    solver.train(model=model,
                 training_set=training_set,
                 n_epochs=hps['N_EPOCHS'],
                 batch_size=hps['BATCH_SIZE'],
                 early_stop=hps['EARLY_STOP'],
                 eval_every=hps['EVAL_EVERY'],
                 start_epoch=start_epoch)

    trainLogger.info("Training finished.")

    return experiment_name
