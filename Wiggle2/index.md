# Welcome to the Wiggle 2: RTX Edition Wiki

*  "The Most Versatile Physics Solution for Rigs of All Kinds.
*  "While optimized for high-density hair, Wiggle 2: RTX Edition is a universal physics engine designed for any bone-based setup. 
*  From complex character tails and dynamic clothing to subtle accessory vibrations, it brings lifelike, procedural movement to every part of your rig.

<video width="100%" controls>
  <source src="assets/blender_ppr2ap3Es5.mp4" type="video/mp4">
  Your browser does not support the video tag.
</video>

*  Wiggle 2: RTX Edition is a professional physics solution engineered to bring dynamic, 
*  lifelike movement to massive hair setups. Optimized for high-end character production, 
*  it serves as a critical pillar in the modern grooming pipeline, especially when paired with GroomForge PRO.

---

## 🆕 What's New in v2.2.7

A maintenance release focused on **addon lifecycle stability** and **Bake Result C safety**, plus a full audit pass across the codebase to remove leftover debug output and dead code.

*   **Fixed: Disabling/re-enabling the addon (or Reload Scripts) could fail.** A version-compatibility issue in how operators/panels checked their own registration state meant that, on Blender 5.x, turning the addon off and back on — or using Blender's "Reload Scripts" — could throw "already registered as a subclass" errors and leave menus/panels in a broken state. Registration now uses proper error handling instead of an unreliable check, so enable/disable/reload cycles are clean.
*   **Fixed: Bake Result C could overwrite your live Sim layer.** Baking used to write directly into the currently selected Sim layer's action. If that layer was still receiving new keyframes (or you baked twice), the bake could stomp on data you wanted to keep. **Bake Result C now always creates a brand-new Sim layer + action for its result** (named "Bake", "Bake 2", ...) and adds it straight into Sim Mix Layers, fully selected and ready to blend — your existing layers are never touched.
*   **Fixed: A rare double-action bug during Bake Result C.** In some cases a single bake could create two action datablocks (e.g. "Bake" and "Bake.001") instead of one, due to a property-update ordering issue. Bake now creates exactly one action per bake.
*   **Fixed: Dope Sheet action selection disappearing during playback.** Selecting a layer's action in the Dope Sheet/Action Editor and pressing Play could cause the selection to silently clear itself, traced to two separate leftover pieces of internal sync logic. Both have been removed — your action selection now survives playback.
*   **Fixed: `Select Enabled` not updating the active bone.** Clicking Select Enabled correctly selected wiggle-enabled bones, but left the properties panel showing a stale bone's settings, which could cause the next checkbox edit to apply to the wrong bone. The active bone is now updated to a genuinely-selected wiggle bone.
*   **Fixed: Angle Limit precision bug.** A duplicate/conflicting property definition for Total Limit between two files has been cleaned up to a single source of truth.
*   **Cleanup**: Removed a per-frame handler that did nothing but walk every armature and bone in the scene every frame (dead code, no behavior change). Removed leftover debug console print statements (Horizontal Lattice rebuild logging). `unregister()` now properly cleans up all custom properties this addon adds to Scene/Object/Bone, so disabling the addon leaves no residue.

---

## 🆕 What's New in v2.2.6

This update is focused entirely on **stability and reliability**. A full pass was made through every setting in the UI to make sure each slider and toggle actually does what its label says — several controls that looked correct in the panel but silently did nothing (or did the wrong thing) have been fixed. No workflow changes are required; your existing rigs and settings will simply behave more correctly after updating.

*   **Fixed: Total Limit ignored at higher Quality.** This was the headline bug. Raising the **Quality** slider (Sim Mix Layers → Bake settings) used to let bones swing far past their **Total Limit** — e.g. a 10° limit could visibly bend 90°. The limit is now enforced correctly no matter how high Quality is set.
*   **Fixed: Freeze/Mute toggles that did nothing.** The Object/Bone **Freeze** icon and the new **Tail/Head Mute** icons (see below) now actually pause physics instead of being cosmetic.
*   **Fixed: Bounce had no effect.** The **Bounce** slider on collisions now genuinely reflects velocity off a collider instead of being ignored.
*   **Fixed: Head-side Wind was silent.** Wind Objects assigned in **Head Settings** now actually push the head, matching Tail behavior.
*   **Fixed: Loop Physics did nothing.** The **Loop Physics** toggle (Bake panel) is now a single, working control — enabling it makes physics continue seamlessly when your timeline loops back to the start during playback, instead of hard-resetting every loop.
*   **Fixed: Bake settings were ignored.** **Preroll**, **Overwrite Current Action**, and **Current Action to NLA** in the Bake panel are now actually applied when you bake, instead of being decorative.
*   **Fixed: Root/Tip Distribution sliders were static.** Dragging the Stiff/Damp **Root** and **Tip** values now updates the whole chain live, the same way the one-click Presets already did.
*   **New: Self Collision.** Bones can now collide with each other on the same rig (capsule-to-capsule), so tails, hair bunches, and skirt panels stop passing through themselves. Opt-in, off by default.
*   **New: Real Sphere / Box / Cylinder / Capsule colliders.** Collision shapes no longer need an actual mesh — pick a simple Empty or object, scale it, and it works as a solid collider directly.
*   **New: Turbulence & Vortex wind fields.** In addition to plain Wind, you can now drive bones with Blender's Turbulence and Vortex force fields for more organic, swirling motion.
*   **New: Disk Point Cache.** Long, expensive simulations can now be cached to disk frame-by-frame, so scrubbing the timeline instantly loads cached results instead of re-simulating from frame 1 every time.
*   **Improved: Horizontal Lattice Stabilizer.** Fixed incorrect bone pairing (it was linking unrelated bones across different chains), added multi-armature support, and added a Stretch Tolerance setting so the stabilizer resists snapping.
*   **Improved: Adaptive Safety Guard.** Now also reacts to fast spinning motion (Rotation Threshold), not just fast linear movement, catching more types of explosive jitter.

---

## ✨ Why RTX Edition?
Experience next-level features that go far beyond the standard Wiggle 2. The RTX Edition is built for professional stability and complex multi-layered setups.

*   **Universal Bone Physics**: Not just for hair. Optimized for **Tails, Clothing, Capes, and any bone-based rig** requiring organic secondary motion.
*   **Modern API Support**: Fully compatible with Blender 4.2 LTS, 5.0, and 5.1+ environments.
*   **Layered Physics System**: A revolutionary system allowing independent physics control for different rig sections (layers) within a single armature.
*   **Advanced Keyframe & Sim Baking**: High-fidelity conversion of simulation data into animation keyframes, ensuring 100% match between Blender and Game Engines.
*   **High-Density Optimization**: Delivers smooth, lag-free viewport performance even with complex rigs involving tens of thousands of bones.

---

## 🚀 Key Synergy: The Master's Workflow
The ultimate character production pipeline is achieved through the seamless integration of these two powerful tools:

*   [**GroomForge PRO**](https://github.io): The industry's most advanced tool for precision creation, UV packing, and Groom Exporting for Unreal Engine 5.
*   **Wiggle 2 RTX**: Adds the final layer of life. It applies high-speed layered physics to GroomForge-generated rigs and provides seamless baking, optimized specifically for **UE5 MetaHuman** pipelines.

Together, they form a "Zero-Waste" ecosystem—from initial strand creation to the final physics-baked export.

---

## 📖 User Guide: Wiggle 2 Physics v2.2.7

### Step 1: Initializing your Physics Stack
To begin using Wiggle 2 RTX, you must first define your animation and simulation layers. The system will not calculate physics until these layers are initialized.

<video width="100%" controls>
  <source src="assets/blender_mV2e3Pqf7z.mp4" type="video/mp4">
</video>
---

#### Critical Prerequisite: Push Down Action
!!! tip
       Before adding layers, your base animation must be pushed down into the NLA (Non-Linear Animation) stack.
       Requirement: The system identifies the "Base_Anim" layer by looking at the pushed-down action strips in the NLA Editor.
       How-to: Select your armature > Go to the NLA Editor > Click the 'Push Down' button on your active action.

![NLA Push Down Guide](assets/image 1.png)

#### Layer Setup
*   Add Base Layer: Click the + button to add your Base_Anim layer.
*   Add Sim Layer: Add one or more Sim_Layers where the RTX physics engine lives.

<video width="100%" controls>
  <source src="assets/blender_yPncVZCJMz.mp4" type="video/mp4">
</video>

---

## 🛠️ Step 2: Stability and Safety Guards

### 1) Use Individual Limits
![Use Individual Limits](assets/limit.png)


*   Toggle: Switches between per-bone individual limits and global settings.
*   Total Limit: Sets the maximum allowed rotation. Higher values allow larger motion; lower values (e.g., 30-60°) prevent mesh clipping.
*   **v2.2.6 fix**: Total Limit now holds correctly at every **Quality** level. Previously, raising Quality could let the bone swing far past the number you set (e.g. 10° behaving like 90°) — that's fixed, so you can safely raise Quality for smoother motion without your limit breaking.
*   **Individual Limits (X / Z)**: Instead of one cone-shaped Total Limit, this lets you set separate up-down (X) and left-right (Z) ranges — useful for things like eyelids or fins that should only move in one plane.

<video width="100%" controls>
  <source src="assets/blender_oJBmdVyU4K.mp4" type="video/mp4">
</video>

---

## 🚀 Step 3: Sim Mix Layers Unified Workflow
This core feature handles keyframes (animation) and simulation (physics) as a single unified data flow.

![Sim Mix Layers UI](assets/image.png)

*   **Bake Result C (Composite Bake)**: Merges keyframes and simulation into a single keyframe track while preserving the exact layer mix weights.
*   **v2.2.7 change**: Bake Result C no longer bakes into your currently selected Sim layer. Every bake now creates a **new Sim layer** (named "Bake", "Bake 2", ...) with its own new action, added to the Sim Mix Layers list and auto-selected. This means baking is always non-destructive to your existing Sim layers, and you can bake the same range multiple times (e.g. to compare variations) without losing earlier results — just delete layers you don't want to keep.
*   **Non-destructive workflow**: Keeps `Base_Anim` (and every existing Sim layer) untouched while cleanly extracting only the simulated result into its own layer.
*   **Game-engine friendly**: Flattens the animation so Unity/Unreal playback matches the Blender viewport 100%.

<video width="100%" controls>
  <source src="assets/blender_K4ijchgKlX.mp4" type="video/mp4">
</video>

---

## 🛡️ Step 4: Wiggle Safety Guard (Adaptive Safety, Self Collision & Lattice)

![Safety Guard UI](assets/image 2.png)

This panel is a "safety net" that prevents uncontrolled behavior such as explosions or infinite jitter during simulation. It now bundles three separate tools:

### 1) Adaptive Safety
1. **Adaptive Safety**: Detects abnormal velocity / excessive energy buildup and applies real-time damping to stabilize.
2. **Sensitivity (e.g., 5.00)**: Higher values react more aggressively to small vibrations.
3. **Rotation Threshold (deg/s)**: New in this update. Works alongside Sensitivity, but watches for *spinning* speed instead of position. If a bone starts spinning faster than this value (degrees per second), the guard damps it down before it explodes. Lower the value if you still see fast "whipping" motion after enabling Adaptive Safety.
4. **Note**: This feature is currently closer to an experimental stage. If motion becomes too stiff, turn it off or lower Sensitivity.

### 2) Self Collision (New)
*   **Self Collision (this object)**: Turn this on per-armature to let a rig's own wiggle bones push each other apart instead of passing through themselves. Great for thick tails, bundled hair strands, or overlapping skirt panels. Off by default because it costs extra performance — only enable it where you actually see clipping.
*   **Margin**: Extra buffer distance added on top of each bone's own Radius setting (found in Tail Settings) before bones are pushed apart. Raise this slightly if bones still visibly touch.

### 3) Horizontal Lattice Stabilizer
*   **Horizontal Lattice Stabilizer**: Enable this to link same-depth bones across neighboring chains (for example, the 3rd bone of every strand in a skirt or hair bunch). This keeps a group of dangling chains moving together as a coherent shape instead of flailing independently.
*   **Lattice Stiffness**: How strongly linked bones pull back toward each other. Higher = tighter, more uniform grouping.
*   **Stretch Tolerance**: How much the lattice link can stretch before it starts correcting. Raise this if you see popping/snapping between linked bones; lower it for a tighter group.
*   **Show Lattice Guide**: Draws the connecting lines in the viewport so you can see which bones are linked.
*   **Active Bone is Lattice Collider**: Mark the currently selected bone as a solid obstacle that other lattice-linked bones should avoid, with an adjustable Radius.
*   **Tip**: This stabilizer runs in real time in the viewport (independent of the frame-by-frame simulation clock), so it reacts immediately as you scrub or drag — it's meant purely as a visual/interactive stabilizer for grouped strands, not as part of the baked physics result.

---

## ⚙️ Step 5: Tail & Head Settings (RTX Optimization Core)

![Tail Settings UI](assets/image 3.png)

The most frequently used core settings area, with many stability improvements in v2.2.6.

1. **Physics parameters**: Mass, Stiff, Stretch, Damp, and Gravity, same as before — improved recovery behavior so stretched bones snap back more predictably.
2. **Freeze this bone side (Mute)**: New Mute icon next to "Tail Settings" and "Head Settings" in the panel header. Toggling it pauses physics for just that side of the bone (it snaps back to follow the animated rest pose) without disabling the whole bone or object — handy for troubleshooting a single misbehaving bone without losing your other settings.
3. **Stiff / Damp Root-Tip Distribution**: Click the small curve icon next to Stiff or Damp to reveal **Root** and **Tip** sliders. This tapers the value along the whole chain below the selected bone (e.g. stiffer near the root, looser at the tip) instead of one flat value for every bone. Dragging Root/Tip now updates the whole chain live as you type.
4. **Wind**: Assign any Blender Force Field object to the Wind slot. As of v2.2.6 this also works correctly on **Head Settings** (previously head-side wind was silently ignored). Three field types are supported:
   *   **Wind**: A steady directional push.
   *   **Turbulence**: Chaotic, randomized motion — good for flags, leaves, loose hair.
   *   **Vortex**: Swirling motion around the field object — good for magical/energy effects or hair caught in a draft.
5. **Collisions**: Choose a collision source next to "Collisions":
   *   **Object**: Collide against a real mesh object.
   *   **Collection**: Collide against every mesh in a Collection at once.
   *   **Sphere / Box / Cylinder / Capsule** (New): Collide against a simple procedural shape instead of a mesh. Just pick any object (an Empty works great) and its own scale defines the size of the shape — no need to model actual collision geometry. Capsule is recommended for limbs, tails, and fingers.
6. **Radius / Friction / Bounce / Sticky**: Fine-tune how the bone reacts on contact. **Bounce** now actually reflects the bone off the surface (previously it had no effect) — 0 means it just stops on contact, higher values make it spring off.
7. **Chain**: Reduces "tip energy buildup" so the far end of long chains doesn't build up excess energy and explode outward.

---

## ⚡ Step 6: Global Utilities

### Quick Presets (One-click Presets)
Applies optimized baseline values for common scenarios: **Jelly / Hair / Heavy / Cloth / Spring / Antenna**.
*   **Tip**: Apply a preset first, then fine-tune in Tail Settings.

<video width="100%" controls>
  <source src="assets/blender_8HdT3mHN1b.mp4" type="video/mp4">
</video>

### Collision Guide Options
*   **Collision Shape**: Box / Cylinder / Capsule (recommended).
*   **Visual Guide**: View collision volume as a viewport wireframe guide.
*   **Interaction**: Adjust Radius / Friction / Bounce / Sticky.
*   **Collisions**: Set targets by Object or Collection.

<video width="100%" controls>
  <source src="assets/blender_zAh4yUSDHu.mp4" type="video/mp4">
</video>

---

## 🔄 Step 7: Loop Physics and Final Bake

![Bake System UI](assets/image 7.png)

*   **Loop Physics**: Smoothly connects physics state from last frame back to the first, so a looping animation doesn't "snap" or reset at the seam. Fixed in v2.2.6 — this toggle previously had no effect; it now works as a single, reliable control. Turn it on before baking a looping cycle (walk cycles, idle animations, etc.).
*   **Preroll**: Number of extra stabilization frames simulated *before* your bake range starts (typically 10~30 frames), so the physics has already "settled" by the time your actual animation begins instead of starting from a stiff, frozen pose. Now actually applied when you bake.
*   **Overwrite Current Action**: When on, bakes directly into your current action instead of creating a new one. When off, a new action is created for you so your original stays untouched. Now actually applied.
*   **Current Action to NLA**: When on, your existing action is pushed down to an NLA track before baking, keeping it safely archived instead of being overwritten. Now actually applied.
*   **Bake (with Auto-Off)**: Automatically turns off realtime physics after the bake completes, so playback shows the baked keyframes instead of re-simulating on top of them.

### Disk Point Cache (New)
For long or heavy simulations, scrubbing the timeline normally forces Wiggle 2 to re-simulate every frame from the start each time you jump backward — which gets slow on long ranges. The new **Disk Point Cache** box lets you:
*   **Bake to Disk Cache**: Simulates your full frame range once and saves every frame's physics result to disk.
*   **Use Cache During Playback**: While enabled, scrubbing/playback loads the saved result instantly instead of re-simulating.
*   **Clear Cache**: Deletes the saved cache — do this after changing any physics settings, since the cache does not auto-detect setting changes.
*   **Directory**: Where cache files are stored (defaults to a folder next to your `.blend` file).

---

**💡 Final Optimization Tip (The "Zero Waste" Rule)**
Use a **256px** guide image in **GroomForge PRO** for optimized UV layouts to ensure **Wiggle 2 RTX** physics run faster and more stable.
