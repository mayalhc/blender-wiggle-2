<!--
Keep this document short & concise,
linking to external resources instead of including content in-line.
See 'release/text/readme.html' for the end user read-me.
-->

# 🚀 Wiggle 2 (Blender 5.0 Patched)

**This is a maintenance fork of the original [Wiggle 2](https://github.com) by shteeve3d.**

### 🌟 Key Updates for 5.0
- **Blender 5.0 Compatibility**: Fixed API issues related to the new animation system and bone data handling.
- **Modular Extension Format**: Converted the single-file addon into a modern Blender Extension structure with `blender_manifest.toml`.
- **Multi-Bone Property Sync**: Added a feature that allows you to toggle checkboxes and update properties for **all selected bones at once**.
- **UI & Performance**: Optimized for the latest Blender UI standards and fixed edge cases for rendering.

### 🛠 How to Install
1. Download this repository as a ZIP.
2. In Blender 5.0, go to `Edit > Preferences > Get Extensions`.
3. Click the menu icon (top right) and select **Install from Disk**.
4. Select the `Wiggle2` folder (or the ZIP containing it).

---


# Wiggle 2

Wiggle 2 is a ground up rewrite of the [wiggle bones add-on](https://github.com/shteeve3d/blender-wiggle) for Blender.

## Features

### New physics logic.
- Wiggling now behaves more realistically, especially when simulating simple ropes or chains.

### Pinning.
- Using a damped track constraint on a wiggling bone pins it to its target, with other bones responding accordingly.
!["Pinning"](/images/pinning.png?raw=true "Pinning")

### Collision support.
- Bones can collide with a specified mesh or collection, and respond with friction, bouncing, or even stickiness.
!["Collision"](/images/collision.png?raw=true "Collision")

### Linking and library overrides.
- Wiggle 2 works properly on library linked assets, with overrides allowing you to tune your wiggle to each scene.

### Baking refinements.
- One click bake converts an objects visible wiggle bones into key frames. Preroll options allow your simulation to settle. Or use it with the timeline looping option for generating seamless wiggle on animated loops. 

### Refreshed interface.
- Everything can be managed from a single panel in the 3d animation view for streamlined, fullscreen workflows.

## Usage
- Install and enable the addon.
- Enable wiggle on the scene. Found in the properties panel of the 3d viewport under the animation tab.

!["Enable Scene"](/images/enable_scene.png?raw=true "Enable Scene")
- Select an armature object.

!["Select Armature"](/images/select_armature.png?raw=true "Select Armature")
- Enable wiggle on the armature. 

!["Enable Armature"](/images/enable_armature.png?raw=true "Enable Armature")
- Select a pose bone.

!["Select Pose Bone"](/images/select_pose_bone.png?raw=true "Select Pose Bone")
- Enable wiggle on the head or tail of the bone. Note: the head will be unavailable if the bone is connected to it's parent (in which case you would just enable the parent's tail.

!["Enable Bone"](/images/enable_bone.png?raw=true "Enable Bone")
- Configure the bone's physics on the dropdowns for the head and tail.

!["Configure Bone"](/images/configure_bone.png?raw=true "Configure Bone")
- Select a collision object or collection to enable the head or tail to collide with it. This will provide further options for tuning collision behaviour.

!["Configure Collision"](/images/configure_collision.png?raw=true "Configure Collision")
- The global utilities offer some convenience functions like resetting physics, quickly selecting all the bones enabled for wiggling, and copying settings between bones. Note that you can always adjust individual settings on multiple selected bones at once. 'Loop physics' will prevent the physics from resetting whenever the timeline loops. 'Quality' refers to how many iterations of the constraint solver are run, which improves rope simulations.

!["Utilities"](/images/utilities.png?raw=true "Utilities")
- Bake Wiggle sub utility will convert the live physics sim into keyframes. It will operate on all visible wiggle bones in the viewport. Overwrite merges the keyframes in the armature's current action, versus creating a new one. Preroll runs the simulation for a specified number of frames, allowing the physics to settle. It also works with 'Loop physics' to help the simulation to settle into a clean animated loop.

!["Bake"](/images/bake.png?raw=true "Bake")

License
-------

Wiggle 2 as a whole is licensed under the GNU General Public License, Version 3.
Individual files may have a different, but compatible license.
