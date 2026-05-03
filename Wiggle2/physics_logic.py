import bpy
import time
from mathutils import Vector
from . import physics_gpu

def wiggle_taper_callback(self, context):
    pass

def wiggle_damp_callback(self, context):
    pass

def apply_taper_to_chain(target, attr, start_val, end_val):
    bone = target.active_pose_bone if hasattr(target, "active_pose_bone") else target
    if not bone: return
    chain = []
    curr = bone
    while curr:
        chain.append(curr)
        if hasattr(curr, "children") and len(curr.children) > 0:
            curr = curr.children[0]
        else: curr = None
    if not chain: return
    for i, b in enumerate(chain):
        t = i / (len(chain) - 1) if len(chain) > 1 else 0
        try: setattr(b, attr, start_val + (end_val - start_val) * t)
        except: pass

def wiggle_solve(context, obj, scene, delta_move):
    try:
        if not getattr(scene, "wiggle_enable", False): return
        if getattr(scene, "wiggle_use_gpu", False):
            physics_gpu.calculate_parallel(obj.pose.bones, scene, delta_move)
    except Exception as e:
        print(f"RTX Engine Warning: {e}")

class WIGGLE_OT_RTX_Turbo(bpy.types.Operator):
    bl_idname = "wiggle.rtx_turbo"
    bl_label = "RTX Turbo Mode"
    _timer = None
    _last_pos = {}

    def modal(self, context, event):
        scene = context.scene
        
        if not getattr(scene, "wiggle_use_gpu", False) or not scene.wiggle_enable:
            return self.cancel(context)

        if event.type == 'TIMER':
            for obj in context.scene.objects:
                if obj.type == 'ARMATURE' and not getattr(obj, "wiggle_mute", False):
                    try:
                        curr_pos = obj.matrix_world.to_translation()
                        if obj.name not in self._last_pos:
                            self._last_pos[obj.name] = curr_pos.copy()
                        
                        delta_move = curr_pos - self._last_pos[obj.name]
                        self._last_pos[obj.name] = curr_pos.copy()
                        
                        wiggle_solve(context, obj, scene, delta_move)
                    except:
                        continue
            
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        
        return {'PASS_THROUGH'}

    def execute(self, context):
        if WIGGLE_OT_RTX_Turbo._timer is not None:
            return self.cancel(context)
        
        context.scene.wiggle_use_gpu = True
        context.scene.wiggle_enable = True
        self._last_pos.clear()
        
        wm = context.window_manager
        WIGGLE_OT_RTX_Turbo._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        
        print(">>> RTX ENGINE: STARTED")
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        print("<<< RTX ENGINE: STOPPED")
        context.scene.wiggle_use_gpu = False
        
        # Reset physics cache to prevent preset value mismatch
        if hasattr(physics_gpu, "reset_cache"):
            physics_gpu.reset_cache()
            
        if WIGGLE_OT_RTX_Turbo._timer:
            context.window_manager.event_timer_remove(WIGGLE_OT_RTX_Turbo._timer)
            WIGGLE_OT_RTX_Turbo._timer = None
        return {'CANCELLED'}

def register():
    bpy.utils.register_class(WIGGLE_OT_RTX_Turbo)

def unregister():
    if WIGGLE_OT_RTX_Turbo._timer:
        WIGGLE_OT_RTX_Turbo._timer = None
    bpy.utils.unregister_class(WIGGLE_OT_RTX_Turbo)
