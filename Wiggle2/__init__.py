import bpy
from . import wiggle_2 
from . import ui_panel 
# Load callback functions for Stiff and Damp from the logic file
from .physics_logic import wiggle_taper_callback, wiggle_damp_callback

def register():
    # 1. PoseBone Properties (Taper Mode Toggles)
    bpy.types.PoseBone.wiggle_stiff_use_dist = bpy.props.BoolProperty(
        name="Use Stiff Dist.",
        default=False,
        description="Enable to use Root-Tip distribution for stiffness; disable to use a single value"
    )

    bpy.types.PoseBone.wiggle_damp_use_dist = bpy.props.BoolProperty(
        name="Use Damp Dist.",
        default=False,
        description="Enable to use Root-Tip distribution for damping; disable to use a single value"
    )

    # 2. Scene Properties for Stiffness (Stiff) Distribution
    bpy.types.Scene.wiggle_stiff_start = bpy.props.FloatProperty(
        name="Stiff Root",
        description="Stiffness value at the start (root) of the bone chain",
        default=10.0,
        min=0.0,
        update=wiggle_taper_callback # Trigger real-time logic on change
    )
    bpy.types.Scene.wiggle_stiff_end = bpy.props.FloatProperty(
        name="Stiff Tip",
        description="Stiffness value at the end (tip) of the bone chain",
        default=10.0,
        min=0.0,
        update=wiggle_taper_callback
    )

    # 3. Scene Properties for Damping (Damp) Distribution
    bpy.types.Scene.wiggle_damp_start = bpy.props.FloatProperty(
        name="Damp Root",
        description="Damping value at the start (root) of the bone chain",
        default=1.0,
        min=0.0,
        update=wiggle_damp_callback # Trigger damping-specific callback
    )
    bpy.types.Scene.wiggle_damp_end = bpy.props.FloatProperty(
        name="Damp Tip",
        description="Damping value at the end (tip) of the bone chain",
        default=1.0,
        min=0.0,
        update=wiggle_damp_callback
    )

    # 4. Register existing modules
    wiggle_2.register() 
    ui_panel.register() 

def unregister():
    # 1. Unregister existing modules
    ui_panel.unregister()
    wiggle_2.unregister()

    # 2. Remove registered properties for memory cleanup
    if hasattr(bpy.types.PoseBone, "wiggle_stiff_use_dist"):
        del bpy.types.PoseBone.wiggle_stiff_use_dist
    if hasattr(bpy.types.PoseBone, "wiggle_damp_use_dist"):
        del bpy.types.PoseBone.wiggle_damp_use_dist

    if hasattr(bpy.types.Scene, "wiggle_stiff_start"):
        del bpy.types.Scene.wiggle_stiff_start
    if hasattr(bpy.types.Scene, "wiggle_stiff_end"):
        del bpy.types.Scene.wiggle_stiff_end
    if hasattr(bpy.types.Scene, "wiggle_damp_start"):
        del bpy.types.Scene.wiggle_damp_start
    if hasattr(bpy.types.Scene, "wiggle_damp_end"):
        del bpy.types.Scene.wiggle_damp_end
