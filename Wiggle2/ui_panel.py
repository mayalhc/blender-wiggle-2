import bpy
from . import wiggle_layers
from mathutils import Vector, Matrix
from .wiggle_2 import WiggleReset, WiggleToggleBBox, WigglePreset

# --- UTILS ---
def flatten(mat):
    dim = len(mat)
    return [mat[j][i] for i in range(dim) for j in range(dim)]

# --- PANELS ---
class WigglePanel:
    bl_category = 'Wiggle 2'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    @classmethod
    def poll(cls, context): return context.object is not None

class WIGGLE_PT_Settings(WigglePanel, bpy.types.Panel):
    bl_label = 'Wiggle 2 Physics'
    bl_idname = "WIGGLE_PT_Settings"

    def draw(self, context):
        layout, scene, obj = self.layout, context.scene, context.object
        layout.separator()

        # 1. Scene Enable Toggle
        row = layout.row()
        row.prop(scene, "wiggle_enable", icon='SCENE_DATA' if scene.wiggle_enable else 'HIDE_ON', text="", emboss=False)
        if not scene.wiggle_enable:
            row.label(text='Scene Muted.')
            return

        # 2. Armature/Object Selection
        row = layout.row()
        if getattr(obj, "wiggle_freeze", False):
            row.prop(obj, 'wiggle_freeze', icon='FREEZE', icon_only=True, emboss=False)
            row.label(text='Frozen (Baked)')
        else:
            row.prop(obj, 'wiggle_mute', icon='ARMATURE_DATA' if not obj.wiggle_mute else 'HIDE_ON', icon_only=True, invert_checkbox=True, emboss=False)
            row.label(text=f"Object: {obj.name}")

        # Check Active Pose Bone
        pb = context.active_pose_bone
        if pb:
            # 3. Individual Bone Mute
            row.prop(pb, 'wiggle_mute', icon='BONE_DATA' if not pb.wiggle_mute else 'HIDE_ON', icon_only=True, invert_checkbox=True, emboss=False)

            # 4. Limit Settings
            layout.separator()
            main_col = layout.column(align=True)
            
            # Safe registration synchronization for Blender 5.1.2 compatibility
            if hasattr(pb, "id_properties_ensure"):
                try: pb.id_properties_ensure()
                except: pass

            use_ind_limits = getattr(pb, "wiggle_use_individual_limits", False)
            
            # Render main toggle switch safely
            if hasattr(pb, "wiggle_use_individual_limits"):
                icon = 'CHECKMARK' if use_ind_limits else 'RADIOBUT_OFF'
                main_col.prop(pb, "wiggle_use_individual_limits", text="Use Individual Limits", toggle=True, icon=icon)
            else:
                main_col.label(text="Individual Limits property not initialized.", icon='INFO')

            inner_box = main_col.box()
            
            if use_ind_limits:
                col = inner_box.column(align=True)
                if hasattr(pb, "wiggle_limit_x"):
                    col.prop(pb, "wiggle_limit_x", text="X (up and down)")
                if hasattr(pb, "wiggle_limit_z"):
                    col.prop(pb, "wiggle_limit_z", text="Z (right and left)")
            else:
                # Bug fix: the previous fallback tried drawing the raw
                # ID-property path ('["wiggle_angle_limit"]') whenever
                # hasattr was False, meant to cover a Blender internal
                # registry lag seen on 5.1.2 - on 5.2 that raw-path draw
                # itself was reported to render as a broken "cannot
                # retrieve"-style field instead of a usable number
                # (rather than the intended graceful warning). Since
                # registration now happens before any class/panel is
                # registered (see register() below), hasattr should
                # always be True by the time this ever draws - if it
                # somehow isn't, fail safely with a plain label instead
                # of attempting the raw path that broke on 5.2.
                if hasattr(pb, "wiggle_angle_limit"):
                    inner_box.prop(pb, "wiggle_angle_limit", text="Total Limit")
                else:
                    inner_box.label(text="Total Limit property not loaded yet - try Reload Scripts.", icon='INFO')


class WIGGLE_PT_SimMixLayer_v3(bpy.types.Panel):
    bl_label = 'Sim Mix Layers'
    bl_idname = "WIGGLE_PT_SimMixLayer_v3"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Wiggle 2'
    bl_parent_id = "WIGGLE_PT_Settings" 

    def draw(self, context):
        layout = self.layout
        # Fix: use the active object, not the pose bone.
        obj = context.active_object

        # Guard against no object selected, or no list property present.
        if not obj:
            layout.label(text="No active object selected")
            return

        # Bug fix (UX): sync_layers() intentionally refuses to touch NLA
        # structure while a strip is in NLA Tweak Mode (e.g. from double-
        # clicking a strip in the NLA editor), to avoid corrupting
        # Blender's own tweak-mode bookkeeping. That also means Layer
        # Weight silently stops doing anything visible until tweak mode
        # is exited, with no indication why - moving the slider looked
        # completely broken. Surface it clearly with a one-click fix.
        if obj.animation_data and getattr(obj.animation_data, "use_tweak_mode", False):
            warn = layout.box()
            warn.alert = True
            warn.label(text="NLA Tweak Mode is active - Layer Weight is frozen", icon='ERROR')
            warn.operator("wiggle.exit_tweak_mode", icon='LOOP_BACK')

        # 1. Top: layer list (references the Object's wiggle_layers).
        row = layout.row()
        # Fix: use obj instead of pb.
        row.template_list("WIGGLE_UL_SimMixLayers", "", obj, "wiggle_layers", obj, "wiggle_layer_index")

        col = row.column(align=True)
        col.operator("wiggle.layer_action", icon='ADD', text="").action = 'ADD'
        col.operator("wiggle.layer_action", icon='REMOVE', text="").action = 'REMOVE'
        col.separator()
        col.operator("wiggle.layer_action", icon='TRIA_UP', text="").action = 'UP'
        col.operator("wiggle.layer_action", icon='TRIA_DOWN', text="").action = 'DOWN'

        # 2. Bottom: detail settings box.
        if hasattr(obj, "wiggle_layers") and len(obj.wiggle_layers) > 0:
            active_layer = obj.wiggle_layers[obj.wiggle_layer_index]
            box = layout.box()
            box.label(text=f"Mix Settings: {active_layer.name}", icon='SETTINGS')

            # Input 1: layer blend weight.
            row = box.row(align=True)
            row.prop(active_layer, "influence", text="Layer Weight (%)", slider=True)

            # Input 2: this layer's actual physics strength.
            row = box.row(align=True)
            row.prop(active_layer, "sim_mix", text="Sim Mix (Physics)", slider=True)
            # Kept the same operator name for the object-mode variant; recheck if it diverges.
            row.operator("wiggle.apply_mix_to_chain", text="", icon='LINKED')

            # Layer type selection.
            row = box.row(align=True)
            row.prop(active_layer, "type", expand=True)

            # Shows the final live physics strength as the sum of
            # (Layer Weight x Sim Mix) across every non-muted Sim layer
            # (0=animation only, 1=full physics).
            if obj.type == 'ARMATURE' and hasattr(obj, "wiggle_layers") and obj.wiggle_layers:
                pb_dbg = next((pb for pb in obj.pose.bones
                               if getattr(pb, "wiggle_tail", False) or getattr(pb, "wiggle_head", False)), None)
                if pb_dbg:
                    box.label(text=f"Live physics strength = {pb_dbg.wiggle_influence:.3f}", icon='INFO')

            # Layout: bake settings/button used to sit right here, in the
            # middle of the Sim Mix Layers panel. That made this panel
            # long and pushed every other panel (Safety/Head/Tail/
            # Utilities) further down, making them harder to find. Moved
            # to Global Utilities > Loop Physics (renamed "Bake"), right
            # below the Loop Physics toggle, to match the actual workflow
            # order (set up layers -> tune physics -> loop -> bake) and
            # keep this panel short.
        else:
            layout.label(text="Add a layer to start", icon='INFO')





# ----------------------------------------------------------------
# This operator should live either in a separate file or in wiggle_2.py's
# register section.
# ----------------------------------------------------------------
class WIGGLE_OT_ApplyMixToChain(bpy.types.Operator):
    """Sums (Layer Weight x Sim Mix) across every Sim Mix layer and
    re-applies it immediately to all wiggle bones on this object,
    refreshing the viewport with the new values without touching the
    timeline."""
    bl_idname = "wiggle.apply_mix_to_chain"
    bl_label = "Refresh Physics Mix"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if not obj or not hasattr(obj, "wiggle_layers"):
            return {'CANCELLED'}

        from . import wiggle_layers as _wl
        if not obj.wiggle_layers:
            self.report({'WARNING'}, "No Sim Mix layers on this object.")
            return {'CANCELLED'}
        weight = _wl.sync_layers(obj)

        try:
            from . import wiggle_2
            wiggle_2.build_list()
            wiggle_2.refresh_influence_blend(obj)
        except Exception:
            pass

        if context.area:
            context.area.tag_redraw()
        self.report({'INFO'}, f"Combined physics strength {weight:.2f} applied.")
        return {'FINISHED'}



class WIGGLE_PT_Tail(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone
    def draw_header(self, context):
        row = self.layout.row(align=True)
        row.prop(context.active_pose_bone, 'wiggle_tail', text="Tail Settings")
        if hasattr(context.active_pose_bone, 'wiggle_tail_mute'):
            row.prop(context.active_pose_bone, 'wiggle_tail_mute', text="", icon='MUTE_IPO_ON', invert_checkbox=False)
    def draw(self, context):
        b, layout = context.active_pose_bone, self.layout
        scene = context.scene
        if not b.wiggle_tail: return
        layout.use_property_split = True
        
        # 1. Mass
        layout.prop(b, 'wiggle_mass')
        
        # 2. Stiff & Distribution
        row_stiff = layout.row(align=True)
        row_stiff.prop(b, "wiggle_stiff_use_dist", text="", icon='IPO_BEZIER', toggle=True)
        if getattr(b, "wiggle_stiff_use_dist", False):
            box = row_stiff.box()
            inner = box.row(align=True)
            inner.prop(scene, "wiggle_stiff_start", text="Root")
            inner.prop(scene, "wiggle_stiff_end", text="Tip")
        else:
            row_stiff.prop(b, 'wiggle_stiff', text="Stiff")
        
        # 3. Stretch
        layout.prop(b, 'wiggle_stretch')
        
        # 4. Damp & Distribution
        row_damp = layout.row(align=True)
        row_damp.prop(b, "wiggle_damp_use_dist", text="", icon='IPO_SINE', toggle=True)
        if getattr(b, "wiggle_damp_use_dist", False):
            box = row_damp.box()
            inner = box.row(align=True)
            inner.prop(scene, "wiggle_damp_start", text="Root")
            inner.prop(scene, "wiggle_damp_end", text="Tip")
        else:
            row_damp.prop(b, 'wiggle_damp', text="Damp")

        # 5. Gravity & Wind
        layout.prop(b, 'wiggle_gravity')
        row_wind = layout.row(align=True); row_wind.prop(b, 'wiggle_wind_ob'); row_wind.prop(b, 'wiggle_wind', text='')
        
        # --- Collision ---
        layout.separator()
        layout.prop(b, 'wiggle_collider_type', text='Collisions')
        if b.wiggle_collider_type == 'Object':
            layout.prop_search(b, 'wiggle_collider', context.scene, 'objects', text=' ')
        elif b.wiggle_collider_type == 'Collection':
            layout.prop_search(b, 'wiggle_collider_collection', bpy.data, 'collections', text=' ')
        elif b.wiggle_collider_type in {'Sphere', 'Box', 'Cylinder', 'Capsule'}:
            layout.prop_search(b, 'wiggle_collider', context.scene, 'objects', text=' ')
            layout.label(text="Size = the object's own scale (no mesh needed)", icon='INFO')

        for p in ['wiggle_radius', 'wiggle_friction', 'wiggle_bounce', 'wiggle_sticky', 'wiggle_chain']:
            if hasattr(b, p): layout.prop(b, p)

class WIGGLE_PT_Head(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone and not context.active_pose_bone.bone.use_connect
    def draw_header(self, context):
        row = self.layout.row(align=True)
        row.prop(context.active_pose_bone, 'wiggle_head', text="Head Settings")
        if hasattr(context.active_pose_bone, 'wiggle_head_mute'):
            row.prop(context.active_pose_bone, 'wiggle_head_mute', text="", icon='MUTE_IPO_ON')
    def draw(self, context):
        b, layout = context.active_pose_bone, self.layout
        if not b.wiggle_head: return
        layout.use_property_split = True
        for p in ['wiggle_mass_head','wiggle_stiff_head','wiggle_stretch_head','wiggle_damp_head','wiggle_gravity_head']: layout.prop(b, p)
        row = layout.row(align=True); row.prop(b,'wiggle_wind_ob_head'); row.prop(b, 'wiggle_wind_head', text='')
        
        layout.separator()
        layout.prop(b, 'wiggle_collider_type_head', text='Collisions')
        if b.wiggle_collider_type_head == 'Object':
            layout.prop_search(b, 'wiggle_collider_head', context.scene, 'objects', text=' ')
        elif b.wiggle_collider_type_head == 'Collection':
            layout.prop_search(b, 'wiggle_collider_collection_head', bpy.data, 'collections', text=' ')
        elif b.wiggle_collider_type_head in {'Sphere', 'Box', 'Cylinder', 'Capsule'}:
            layout.prop_search(b, 'wiggle_collider_head', context.scene, 'objects', text=' ')
            layout.label(text="Size = the object's own scale (no mesh needed)", icon='INFO')

        for p in ['wiggle_radius_head','wiggle_friction_head','wiggle_bounce_head','wiggle_sticky_head', 'wiggle_chain_head']:
            if hasattr(b, p): layout.prop(b, p)

        layout.separator()
        if hasattr(b, 'wiggle_max_offset_head'):
            layout.prop(b, 'wiggle_max_offset_head', text="Max Offset")

class WIGGLE_PT_Utilities(WigglePanel, bpy.types.Panel):
    bl_label = 'Global Utilities'
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {"DEFAULT_CLOSED"}
    def draw(self, context):
        layout, scene = self.layout, context.scene
        col = layout.column(align=True)
        if hasattr(bpy.ops.wiggle, 'copy'): col.operator('wiggle.copy', text="Copy to Selected", icon='PASTEDOWN')
        if hasattr(bpy.ops.wiggle, 'select'): col.operator('wiggle.select', text="Select Active", icon='RESTRICT_SELECT_OFF')
        col.operator('wiggle.reset', text="HARD RESET (Cache & Action)", icon='TRASH') 
        
        layout.separator(); box = layout.box(); box.label(text="Quick Presets:")
        r1, r2 = box.row(align=True), box.row(align=True)
        r1.operator("wiggle.preset", text="Jelly").type = 'JELLY'
        r1.operator("wiggle.preset", text="Hair").type = 'HAIR'
        r1.operator("wiggle.preset", text="Heavy").type = 'HEAVY'
        r2.operator("wiggle.preset", text="Cloth").type = 'CLOTH'
        r2.operator("wiggle.preset", text="Spring").type = 'SPRING'
        r2.operator("wiggle.preset", text="Antenna").type = 'ANTENNA'
        
        layout.separator()
        row = layout.row(align=True)

        if hasattr(scene, "wiggle_guide_shape"):
            row.prop(scene, "wiggle_guide_shape", text="")
            guide_shape = scene.wiggle_guide_shape
        else:
            guide_shape = 'BOX'

        g_icon = 'MESH_CUBE'
        if guide_shape == 'CYLINDER':
            g_icon = 'MESH_CYLINDER'
        elif guide_shape == 'CAPSULE':
            g_icon = 'MESH_CAPSULE'

        row.operator("wiggle.toggle_bbox", text="Visual Guide", icon=g_icon)

                
        if hasattr(scene, "wiggle"):
            # Deduplicated: the Loop Physics toggle is now unified with
            # the Bake panel's scene.wiggle_use_loop (which actually wires
            # into wiggle_post's reset logic). scene.wiggle.loop is kept
            # around as an unused legacy field for backward compatibility.
            layout.prop(scene.wiggle, 'iterations', text="Quality")

class WIGGLE_PT_Bake(WigglePanel, bpy.types.Panel):
    # Cleanup: bake settings/button and Disk Point Cache used to live
    # inside the Sim Mix Layers panel (see WIGGLE_PT_SimMixLayer_v3),
    # which made that panel long and pushed every panel after it further
    # down. Moved everything bake-related here, below the Loop Physics
    # toggle, to match the actual workflow order (set up layers -> tune
    # physics -> loop -> bake) and to keep it all in one predictable
    # place at the end.
    bl_label = 'Bake'
    bl_parent_id = 'WIGGLE_PT_Utilities'
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "wiggle"): return

        scene = context.scene
        w = scene.wiggle
        layout.use_property_split = True

        # Loop Physics setting.
        layout.prop(scene, "wiggle_use_loop", text="Loop Physics", icon='LOOP_FORWARDS', toggle=True)

        layout.separator()
        bake_box = layout.box()
        bake_box.label(text="Bake", icon='RENDER_ANIMATION')
        bake_box.use_property_split = True
        bake_box.prop(w, 'preroll')
        bake_box.prop(w, 'bake_overwrite')
        nla_row = bake_box.row()
        nla_row.enabled = not w.bake_overwrite
        nla_row.prop(w, 'bake_nla')
        bake_row = layout.row()
        bake_row.scale_y = 1.2
        bake_row.operator("wiggle.bake_combined", icon='RENDER_ANIMATION', text="Bake Result C")

        if hasattr(scene, "wiggle_cache_enable"):
            layout.separator()
            cache_box = layout.box()
            cache_box.label(text="Disk Point Cache (for scrubbing)", icon='FILE_CACHE')
            cache_box.prop(scene, "wiggle_cache_dir", text="Directory")
            cache_box.prop(scene, "wiggle_cache_enable", text="Use Cache During Playback")
            cache_row = cache_box.row(align=True)
            cache_row.operator("wiggle.bake_cache", icon='REC')
            cache_row.operator("wiggle.clear_cache", icon='TRASH')


class WIGGLE_PT_Safety(WigglePanel, bpy.types.Panel):
    bl_label = 'Wiggle Safety Guard'
    bl_idname = "WIGGLE_PT_Safety"
    bl_parent_id = "WIGGLE_PT_Settings"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.object

        # --- 1. Original Adaptive Safety Guard Block ---
        if not hasattr(scene, "wiggle_adaptive_damping"):
            layout.label(text="Error: Properties not registered.", icon='ERROR')
            return

        box = layout.box()
        box.prop(scene, "wiggle_adaptive_damping", text="Adaptive Safety", icon='CHECKMARK')
        col = box.column()
        col.enabled = scene.wiggle_adaptive_damping
        col.prop(scene, "wiggle_safety_threshold", text="Sensitivity", slider=True)
        if hasattr(scene, "wiggle_safety_rot_threshold"):
            col.prop(scene, "wiggle_safety_rot_threshold", text="Rotation Threshold (deg/s)")
        col.label(text="Auto-damps explosive motion (position AND fast spins).")

        # --- Self Collision (point-based, per-object, off by default) ---
        if obj and hasattr(obj, "wiggle_self_collide"):
            layout.separator()
            box_sc = layout.box()
            box_sc.prop(obj, "wiggle_self_collide", text="Self Collision (this object)", icon='MOD_PHYSICS')
            col_sc = box_sc.column()
            col_sc.enabled = obj.wiggle_self_collide
            col_sc.prop(obj, "wiggle_self_collide_margin", text="Margin")
            col_sc.label(text="Capsule-capsule between wiggle_tail bones (radius = Radius).", icon='INFO')

        # --- 2. Horizontal Lattice Stabilizer ---
        if not hasattr(scene, "wiggle_use_lattice"):
            return

        layout.separator()
        box_lattice = layout.box()
        box_lattice.prop(scene, "wiggle_use_lattice", text="Horizontal Lattice Stabilizer", icon='GRID')

        col_lattice = box_lattice.column(align=True)
        col_lattice.enabled = scene.wiggle_use_lattice
        col_lattice.prop(scene, "wiggle_lattice_stiffness", text="Lattice Stiffness", slider=True)
        if hasattr(scene, "wiggle_lattice_stretch"):
            col_lattice.prop(scene, "wiggle_lattice_stretch", text="Stretch Tolerance", slider=True)
        col_lattice.prop(scene, "wiggle_lattice_show_debug", text="Show Lattice Guide", icon='RESTRICT_VIEW_OFF')
        col_lattice.label(text="Pairs same-depth wiggle bones (skirts, hair bunches).", icon='INFO')

        pb = context.active_pose_bone
        if pb and hasattr(pb, "wiggle_is_collider"):
            row = box_lattice.row(align=True)
            row.prop(pb, "wiggle_is_collider", text="Active Bone is Lattice Collider", toggle=True)
            if pb.wiggle_is_collider:
                row.prop(pb, "wiggle_collider_radius", text="Radius")

# --- REGISTRATION ---

classes = (
    WIGGLE_PT_Settings,          # main parent panel
    WIGGLE_PT_SimMixLayer_v3,    # fix: object-mode-aware panel
    WIGGLE_PT_Safety,            # safety guard panel
    WIGGLE_PT_Head,              # head settings panel
    WIGGLE_PT_Tail,              # tail settings panel
    WIGGLE_PT_Utilities,         # utilities parent panel
    WIGGLE_PT_Bake,              # bake panel

    WIGGLE_OT_ApplyMixToChain,    # operator
)

def register():
    # Bug fix: properties (Scene/PoseBone) used to be registered AFTER
    # the panel/operator classes below. In normal single-threaded
    # execution register() always finishes before Blender's event loop
    # can trigger a redraw, so this shouldn't matter - but users have
    # reported Total Limit intermittently showing a broken "cannot
    # retrieve"-style field on Blender 5.2 right after enabling the
    # addon, which didn't happen on 5.1. That could only happen if a
    # panel draws while wiggle_angle_limit isn't registered on
    # PoseBone yet, which points at some difference in how 5.2's
    # extension loader sequences registration/redraws versus 5.1's.
    # Registering properties first removes that window entirely,
    # regardless of the exact cause.
    # 1. Register Scene-level properties.
    bpy.types.Scene.wiggle_adaptive_damping = bpy.props.BoolProperty(
        name="Safety Guard", default=True, 
        description="Automatic Bone Pop Prevention"
    )
    bpy.types.Scene.wiggle_safety_threshold = bpy.props.FloatProperty(
        name="Sensitivity", default=1.0, min=0.1, max=10.0,
        description="Defense Trigger Sensitivity"
    )
    bpy.types.Scene.wiggle_safety_rot_threshold = bpy.props.FloatProperty(
        name="Rotation Threshold", default=180.0, min=10.0, max=1080.0,
        description="Object rotation speed (degrees/second) that triggers extra damping - "
                    "catches fast spins/whips that pure position tracking misses"
    )
    bpy.types.Scene.wiggle_use_loop = bpy.props.BoolProperty(
        name="Loop Physics", default=False,
        description="Transfer physics from the last frame to the first to create a loop"
    )
    bpy.types.Scene.wiggle_guide_shape = bpy.props.EnumProperty(
        name="Shape", 
        items=[('BOX', "Box", ""), ('CYLINDER', "Cylinder", ""), ('CAPSULE', "Capsule", "")], 
        default='BOX'
    )

    # 2. Register PoseBone-level properties.
    # wiggle_influence is registered in wiggle_layers.py (default=1.0). Do not register it again here.
    bpy.types.PoseBone.wiggle_use_individual_limits = bpy.props.BoolProperty(
        name="Use Individual Limits", default=False,
        description="Set X/Z limits separately. (Alt + Click to sync.)"
    )
    bpy.types.PoseBone.wiggle_angle_limit = bpy.props.FloatProperty(
        name="Angle Limit", default=180.0, min=0.0, max=180.0, precision=1,
        description="Press Alt + Enter to apply change to the entire skeleton"
    )
    bpy.types.PoseBone.wiggle_limit_x = bpy.props.FloatProperty(
        name="X Limit", min=0.0, max=180.0, default=90.0, precision=1
    )
    bpy.types.PoseBone.wiggle_limit_z = bpy.props.FloatProperty(
        name="Z Limit", min=0.0, max=180.0, default=90.0, precision=1
    )
    bpy.types.PoseBone.wiggle_max_offset_head = bpy.props.FloatProperty(
        name="Max Offset", default=0.0, min=0.0,
        description="Clamp how far the floating head may drift from its animated rest position (0 = unlimited)"
    )

    # 3. Register classes (panels/operators), now that every property
    # they might draw on their very first invocation already exists.
    for cls in classes:
        # Bug fix: hasattr(bpy.types, cls.__name__) was meant to check
        # "is this already registered", but measured directly on
        # Blender 5.x, this hasattr is always False for Operator/Panel
        # classes regardless of actual registration state
        # (register_class/bpy.ops themselves work fine). So this guard
        # never did anything, and just produced "already registered"
        # exceptions on re-registration (Reload Scripts, etc). Use
        # try/except and judge by the actual exception instead.
        try:
            bpy.utils.register_class(cls)
        except (RuntimeError, ValueError) as e:
            pass

def unregister():
    # 1. Unregister classes.
    # Bug fix: the hasattr(bpy.types, cls.__name__) guard doesn't reflect
    # actual Operator/Panel registration state at all on Blender 5.x
    # (always False), so this operator/panel never actually got
    # unregister_class'd and stuck around. This was the cause of
    # "already registered as a subclass" failing re-registration after
    # disabling and re-enabling the addon. Switched to try/except.
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass

    # 2. Delete Scene properties.
    props_scene = [
        "wiggle_adaptive_damping", "wiggle_safety_threshold", "wiggle_safety_rot_threshold",
        "wiggle_use_loop", "wiggle_guide_shape"
    ]
    for p in props_scene:
        if hasattr(bpy.types.Scene, p): delattr(bpy.types.Scene, p)

    # 3. Delete PoseBone properties.
    props_bone = [
        "wiggle_use_individual_limits",
        "wiggle_angle_limit", "wiggle_limit_x", "wiggle_limit_z", "wiggle_max_offset_head"
    ]
    for p in props_bone:
        if hasattr(bpy.types.PoseBone, p): delattr(bpy.types.PoseBone, p)