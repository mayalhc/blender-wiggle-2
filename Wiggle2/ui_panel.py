# START OF REVISION #
import bpy
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
    bl_label = 'Wiggle 2 Main'
    bl_idname = "WIGGLE_PT_Settings"
    def draw(self, context):
        layout, scene, obj = self.layout, context.scene, context.object
        row = layout.row()
        row.prop(scene, "wiggle_enable", icon='SCENE_DATA' if scene.wiggle_enable else 'HIDE_ON', text="", emboss=False)
        if not scene.wiggle_enable: row.label(text='Scene Muted.'); return
        row = layout.row()
        if getattr(obj, "wiggle_freeze", False): 
            row.prop(obj, 'wiggle_freeze', icon='FREEZE', icon_only=True, emboss=False)
            row.label(text='Frozen (Baked)')
        else:
            row.prop(obj, 'wiggle_mute', icon='ARMATURE_DATA' if not obj.wiggle_mute else 'HIDE_ON', icon_only=True, invert_checkbox=True, emboss=False)
            pb = context.active_pose_bone
            if pb: row.prop(pb, 'wiggle_mute', icon='BONE_DATA' if not pb.wiggle_mute else 'HIDE_ON', icon_only=True, invert_checkbox=True, emboss=False)

class WIGGLE_PT_Tail(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone
    def draw_header(self, context):
        self.layout.prop(context.active_pose_bone, 'wiggle_tail', text="Tail Settings")
    def draw(self, context):
        b, layout = context.active_pose_bone, self.layout
        if not b.wiggle_tail: return
        layout.use_property_split = True
        for p in ['wiggle_mass', 'wiggle_stiff', 'wiggle_stretch', 'wiggle_damp', 'wiggle_gravity']:
            layout.prop(b, p)
        row = layout.row(align=True); row.prop(b, 'wiggle_wind_ob'); row.prop(b, 'wiggle_wind', text='')
        
        layout.separator()
        layout.prop(b, 'wiggle_collider_type', text='Collisions')
        if b.wiggle_collider_type == 'Object':
            layout.prop_search(b, 'wiggle_collider', context.scene, 'objects', text=' ')
        elif b.wiggle_collider_type == 'Collection':
            layout.prop_search(b, 'wiggle_collider_collection', bpy.data, 'collections', text=' ')
        elif b.wiggle_collider_type in {'Box', 'Cylinder', 'Capsule'}:
            row = layout.row()
            icon_map = {'Box': 'MESH_CUBE', 'Cylinder': 'MESH_CYLINDER', 'Capsule': 'MESH_CAPSULE'}
            row.label(text=f"Preview Mode: {b.wiggle_collider_type}", icon=icon_map.get(b.wiggle_collider_type, 'NONE'))
            
        for p in ['wiggle_radius', 'wiggle_friction', 'wiggle_bounce', 'wiggle_sticky', 'wiggle_limit_angle', 'wiggle_chain']:
            if hasattr(b, p): layout.prop(b, p)

class WIGGLE_PT_Head(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone and not context.active_pose_bone.bone.use_connect
    def draw_header(self, context):
        self.layout.prop(context.active_pose_bone, 'wiggle_head', text="Head Settings")
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
        elif b.wiggle_collider_type_head in {'Box', 'Cylinder', 'Capsule'}:
            row = layout.row()
            icon_map = {'Box': 'MESH_CUBE', 'Cylinder': 'MESH_CYLINDER', 'Capsule': 'MESH_CAPSULE'}
            row.label(text=f"Preview Mode: {b.wiggle_collider_type_head}", icon=icon_map.get(b.wiggle_collider_type_head, 'NONE'))
            
        for p in ['wiggle_radius_head','wiggle_friction_head','wiggle_bounce_head','wiggle_sticky_head', 'wiggle_limit_angle_head', 'wiggle_chain_head']:
            if hasattr(b, p): layout.prop(b, p)

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
        
        layout.separator(); row = layout.row(align=True)
        row.prop(scene, "wiggle_guide_shape", text="")
        g_icon = 'MESH_CUBE'
        if scene.wiggle_guide_shape == 'CYLINDER': g_icon = 'MESH_CYLINDER'
        elif scene.wiggle_guide_shape == 'CAPSULE': g_icon = 'MESH_CAPSULE'
        row.operator("wiggle.toggle_bbox", text="Visual Guide", icon=g_icon)
        
        if hasattr(scene, "wiggle"):
            layout.prop(scene.wiggle, 'loop', text="Loop Physics")
            layout.prop(scene.wiggle, 'iterations', text="Quality")

class WIGGLE_PT_Bake(WigglePanel, bpy.types.Panel):
    bl_label = 'Bake'
    bl_parent_id = 'WIGGLE_PT_Utilities'
    bl_options = {"DEFAULT_CLOSED"}
    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "wiggle"): return
        w = context.scene.wiggle
        layout.use_property_split = True
        layout.prop(w, 'preroll'); layout.prop(w, 'bake_overwrite')
        row = layout.row(); row.enabled = not w.bake_overwrite; row.prop(w, 'bake_nla')
        if hasattr(bpy.ops.wiggle, 'bake'): layout.operator('wiggle.bake', icon='REC')

# --- REGISTRATION ---
classes = (
    WIGGLE_PT_Settings, 
    WIGGLE_PT_Head, 
    WIGGLE_PT_Tail, 
    WIGGLE_PT_Utilities, 
    WIGGLE_PT_Bake
)

def register():
    # 중복 정의 방지를 위해 체크 후 등록
    if not hasattr(bpy.types.Scene, "wiggle_guide_shape"):
        bpy.types.Scene.wiggle_guide_shape = bpy.props.EnumProperty(
            name="Shape", 
            items=[('BOX', "Box", ""), ('CYLINDER', "Cylinder", ""), ('CAPSULE', "Capsule", "")], 
            default='BOX'
        )
    
    for cls in classes:
        # 이미 등록된 클래스인지 체크 (안전 장치)
        if not hasattr(bpy.types, cls.__name__):
            bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)
    
    if hasattr(bpy.types.Scene, "wiggle_guide_shape"):
        del bpy.types.Scene.wiggle_guide_shape

if __name__ == "__main__":
    register()
