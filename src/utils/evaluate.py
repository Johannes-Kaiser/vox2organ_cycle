""" Evaluation of a model """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

from enum import IntEnum
import os
import logging

import numpy as np
import torch
from tqdm import tqdm
from pytorch3d.loss import chamfer_distance

from utils.modes import ExecModes
from utils.mesh import Mesh
from utils.utils import (
    create_mesh_from_voxels,
    unnormalize_vertices,
    sample_inner_volume_in_voxel)
from utils.logging import (
    write_img_if_debug,
    measure_time)
from models.voxel2mesh import Voxel2Mesh

class EvalMetrics(IntEnum):
    """ Supported evaluation metrics """
    # Jaccard score/ Intersection over Union from voxel prediction
    JaccardVoxel = 1

    # Chamfer distance between ground truth mesh and predicted mesh
    Chamfer = 2

    # Jaccard score/ Intersection over Union from mesh prediction
    JaccardMesh = 3

@measure_time
def JaccardMeshScore(pred, data, n_classes):
    """ Jaccard averaged over classes ignoring background. The mesh prediction
    is compared against the voxel ground truth.
    """
    _, voxel_target, _ = data
    shape = torch.tensor(voxel_target.shape)[None]
    vertices, faces = Voxel2Mesh.pred_to_verts_and_faces(pred)
    voxel_pred = torch.zeros_like(voxel_target, dtype=torch.long)
    for c in range(1, n_classes):
        # Only mesh of last step considered
        unnorm_verts = unnormalize_vertices(vertices[-1][c].squeeze(), shape)
        pv = Mesh(unnorm_verts,
                  faces[-1][c]).get_occupied_voxels(shape.squeeze().cpu().numpy())
        pv_flip = np.flip(pv, axis=1)  # convert x,y,z -> z, y, x
        # Potentially overwrites previous class prediction if overlapping
        voxel_pred[pv_flip[:,0], pv_flip[:,1], pv_flip[:,2]] = c

    # Strip off one layer of voxels
    voxel_pred_inner = sample_inner_volume_in_voxel(voxel_pred)
    write_img_if_debug(voxel_pred_inner.cpu().numpy(),
                       voxel_target.cpu().numpy())

    # j_coo = Jaccard_from_Coords(pred_voxels, target_voxels, n_classes)
    j_vox = Jaccard(voxel_pred_inner.cuda(), voxel_target.cuda(), n_classes)

    return j_vox

@measure_time
def JaccardVoxelScore(pred, data, n_classes):
    """ Jaccard averaged over classes ignoring background """
    voxel_pred = Voxel2Mesh.pred_to_voxel_pred(pred)
    _, voxel_label, _ = data # chop

    return Jaccard(voxel_pred, voxel_label, n_classes)

@measure_time
def Jaccard_from_Coords(pred, target, n_classes):
    """ Jaccard/ Intersection over Union from lists of occupied voxels. This
    necessarily implies that all occupied voxels belong to one class.

    Attention: This function is usally a lot slower than 'Jaccard' (probably
    because it does not exploit cuda).

    :param pred: Shape (C, V, 3)
    :param target: Shape (C, V, 3)
    :param n_classes: C
    """
    ious = []
    # Ignoring background class 0
    for c in range(1, n_classes):
        if isinstance(pred[c], torch.Tensor):
            pred[c] = pred[c].cpu().numpy()
        if isinstance(target[c], torch.Tensor):
            target[c] = target[c].cpu().numpy()
        intersection = 0
        for co in pred[c]:
            if any(np.equal(target[c], co).all(1)):
                intersection += 1

        union = pred[c].shape[0] + target[c].shape[0] - intersection

        # +1 for smoothing (no division by 0)
        ious.append(float(intersection + 1) / float(union + 1))

    return np.sum(ious)/(n_classes - 1)

@measure_time
def Jaccard(pred, target, n_classes):
    """ Jaccard/Intersection over Union from 3D voxel volumes """
    ious = []
    # Ignoring background class 0
    for c in range(1, n_classes):
        pred_idxs = pred == c
        target_idxs = target == c
        intersection = pred_idxs[target_idxs].long().sum().data.cpu()
        union = pred_idxs.long().sum().data.cpu() + \
                    target_idxs.long().sum().data.cpu() -\
                    intersection
        # +1 for smoothing (no division by 0)
        ious.append(float(intersection + 1) / float(union + 1))

    # Return average iou over classes ignoring background
    return np.sum(ious)/(n_classes - 1)

def ChamferScore(pred, data, n_classes):
    """ Chamfer distance averaged over classes

    Note: In contrast to the ChamferLoss, where the Chamfer distance is computed
    between the predicted loss and randomly sampled surface points, here the
    Chamfer distance is computed between the predicted mesh and the ground
    truth mesh. """
    pred_vertices, _ = Voxel2Mesh.pred_to_verts_and_faces(pred)
    _, _, gt_mesh = data # chop
    gt_vertices = gt_mesh.vertices.cuda()[None] # currently only one class
    chamfer_scores = []
    for c in range(1, n_classes):
        pred_vertices = pred_vertices[-1][c] # only consider last mesh step
        chamfer_scores.append(chamfer_distance(pred_vertices,
                                               gt_vertices)[0].cpu().item())

    # Average over classes
    return np.sum(chamfer_scores) / float(n_classes - 1)

class ModelEvaluator():
    """ Class for evaluation of models.

    :param eval_dataset: The dataset split that should be used for evaluation.
    :param save_dir: The experiment directory where data can be saved.
    :param n_classes: Number of classes.
    :param eval_metrics: A list of metrics to use for evaluation.
    :param mc_step_size: Marching cubes step size.
    """
    def __init__(self, eval_dataset, save_dir, n_classes, eval_metrics,
                 mc_step_size=1, **kwargs):
        self._dataset = eval_dataset
        self._save_dir = save_dir
        self._n_classes = n_classes
        self._eval_metrics = eval_metrics
        self._mc_step_size = mc_step_size

        self._mesh_dir = os.path.join(self._save_dir, "meshes")
        if not os.path.isdir(self._mesh_dir):
            os.mkdir(self._mesh_dir)

        self._metricHandler = {
            EvalMetrics.JaccardVoxel.name: JaccardVoxelScore,
            EvalMetrics.JaccardMesh.name: JaccardMeshScore,
            EvalMetrics.Chamfer.name: ChamferScore
        }

    def evaluate(self, model, epoch, save_meshes=5):
        results_all = {}
        for m in self._eval_metrics:
            results_all[m] = []
        # Iterate over data split
        with torch.no_grad():
            for i in tqdm(range(len(self._dataset)), desc="Evaluate..."):
                data = self._dataset.get_item_and_mesh_from_index(i)
                data_voxel2mesh = Voxel2Mesh.convert_data_to_voxel2mesh_data(data,
                                                                  self._n_classes,
                                                                  ExecModes.TEST)
                pred = model(data_voxel2mesh)

                for metric in self._eval_metrics:
                    res = self._metricHandler[metric](pred, data, self._n_classes)
                    results_all[metric].append(res)

                if i < save_meshes: # Store meshes for visual inspection
                    filename =\
                            self._dataset.get_file_name_from_index(i).split(".")[0]
                    self.store_meshes(pred, data, filename, epoch)

        # Just consider means over evaluation set
        results = {k: np.mean(v) for k, v in results_all.items()}

        return results

    def store_meshes(self, pred, data, filename, epoch):
        """ Save predicted meshes and ground truth created with marching
        cubes
        """
        _, voxel_label, _ = data # chop
        for c in range(1, self._n_classes):
            # Label
            gt_filename = filename + "_class" + str(c) + "_gt.ply"
            if not os.path.isfile(gt_filename):
                # gt file does not exist yet
                voxel_label_class = voxel_label.cpu()
                voxel_label_class[voxel_label != c] = 0
                gt_mesh = create_mesh_from_voxels(voxel_label_class,
                                                  self._mc_step_size)
                gt_mesh.store(os.path.join(self._mesh_dir, gt_filename))

            # Mesh prediction
            pred_mesh_filename = filename + "_epoch" + str(epoch) +\
                "_class" + str(c) + "_meshpred.ply"
            vertices, faces = Voxel2Mesh.pred_to_verts_and_faces(pred)
            vertices, faces = vertices[-1][c], faces[-1][c]
            pred_mesh = Mesh(vertices.squeeze().cpu(),
                             faces.squeeze().cpu())
            pred_mesh.store(os.path.join(self._mesh_dir, pred_mesh_filename))

            # Voxel prediction
            pred_voxel_filename = filename + "_epoch" + str(epoch) +\
                "_class" + str(c) + "_voxelpred.ply"
            pred_voxel = Voxel2Mesh.pred_to_voxel_pred(pred)
            try:
                mc_pred_mesh = create_mesh_from_voxels(pred_voxel,
                                                  self._mc_step_size).to_trimesh(process=True)
                mc_pred_mesh.export(os.path.join(self._mesh_dir, pred_voxel_filename))
            except RuntimeError as e:
                logging.getLogger(ExecModes.TEST.name).warning(\
                       "In voxel prediction for file: %s: %s ", filename, e)