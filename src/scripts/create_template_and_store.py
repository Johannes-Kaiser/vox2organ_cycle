
""" Create a mesh template and store it """

__author__ = "Fabi Bongratz"
__email__ = "fabi.bongratz@gmail.com"

from data.cortex import Cortex

structure_type = 'white_matter'

template_path = "../supplementary_material/" + structure_type + "/cortex_" + structure_type + "_convex.obj"

print("Creating dataset...")
dataset, _, _ = Cortex.split("/mnt/nas/Data_Neuro/MALC_CSR/",
                             1532,
                             (100, 0, 0),
                             False,
                             "../misc",
                             patch_size=(192, 224, 192),
                             structure_type=structure_type,
                             mesh_target_type='mesh',
                             n_ref_points_per_structure=None,
                             patch_mode=False)
print("Dataset created.")
print("Creating template...")

path = dataset.store_convex_cortex_template(template_path, n_min_points=100000,
                                            n_max_points=140000)

if path is not None:
    print("Template stored at " + path)
