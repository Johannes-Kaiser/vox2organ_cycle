
""" Convenience script to assemble a template from individual files. This file
can be copied into a folder where the files "lh_white.ply", "rh_white.ply",
"lh_pial.ply", and "rh_pial.ply" exist. """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

import trimesh
from trimesh.scene.scene import Scene

lh_white = trimesh.load("lh_white.ply")
rh_white = trimesh.load("rh_white.ply")
lh_pial = trimesh.load("lh_pial.ply")
rh_pial = trimesh.load("rh_pial.ply")

scene = Scene()

scene.add(lh_white, geom_name="lh_white")
scene.add(rh_white, geom_name="rh_white")
scene.add(lh_pial, geom_name="lh_pial")
scene.add(rh_pial, geom_name="rh_pial")

scene.export("PLACE_FILE_NAME_HERE.obj")
