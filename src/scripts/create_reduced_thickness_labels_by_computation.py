
""" Create per-vertex cortical thickness ground truth for freesurfer meshes
by computing the orthogonal distance of each vertex to the respective other
surface."""

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

import os

import nibabel as nib
import torch
import trimesh
from pytorch3d.structures import Meshes, Pointclouds

from data.cortex_labels import valid_MALC_ids
from utils.cortical_thickness import _point_mesh_face_distance_unidirectional

structures = ("lh_white", "rh_white", "lh_pial", "rh_pial")
partner = {"lh_white": 2, "rh_white": 3, "lh_pial": 0, "rh_pial": 1}
suffix = "_reduced_0.3"
RAW_DATA_DIR = "/mnt/nas/Data_Neuro/MALC_CSR/"
PREPROCESSED_DIR = "/home/fabianb/data/preprocessed/MALC_CSR/"

files = valid_MALC_ids(os.listdir(RAW_DATA_DIR))

# Compare thickness in stored files to thickness computed by orthogonal
# projection
for fn in files:
    prep_dir = os.path.join(PREPROCESSED_DIR, fn)
    if not os.path.isdir(prep_dir):
        os.mkdir(prep_dir)
    for struc in structures:
        # Filenames
        red_mesh_name = os.path.join(
            RAW_DATA_DIR, fn, struc + suffix + ".stl"
        )
        red_partner_mesh_name = os.path.join(
            RAW_DATA_DIR, fn, structures[partner[struc]] + suffix + ".stl"
        )

        # Load meshes
        red_mesh = trimesh.load(red_mesh_name)
        red_mesh_partner = trimesh.load(red_partner_mesh_name)

        # Compute thickness by orthogonal projection for full meshes
        red_vertices = torch.from_numpy(red_mesh.vertices).float().cuda()
        red_faces = torch.from_numpy(red_mesh.faces).int().cuda()
        partner_vertices = torch.from_numpy(
            red_mesh_partner.vertices
        ).float().cuda()
        partner_faces = torch.from_numpy(red_mesh_partner.faces).long().cuda()

        pntcloud = Pointclouds([red_vertices])
        partner_mesh = Meshes([partner_vertices], [partner_faces])

        point_to_face = _point_mesh_face_distance_unidirectional(
            pntcloud, partner_mesh
        ).cpu().squeeze().numpy()

        # Write
        red_th_name = os.path.join(
            PREPROCESSED_DIR, fn, struc + suffix + ".thickness"
        )
        nib.freesurfer.io.write_morph_data(red_th_name, point_to_face)

        print("Created label for file ", fn + "/" + struc)
