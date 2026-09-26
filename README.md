# CV Auto Track

[English](README.md) | [日本語](README_ja.md)

## Overview

CV Auto Track adds fast OpenCV-powered auto tracking to Blender's Movie Clip Editor.

It is designed for a simple preset-based workflow: open footage, choose a preset, and run automatic tracking. The add-on detects 2D feature points, tracks them through the clip, filters weak candidates, balances screen coverage, and bakes the result as standard Blender Movie Tracking markers.

## Features

- **Fast OpenCV auto tracking:** Generates many 2D tracking markers quickly.
- **Simple presets:** Start from footage-oriented presets such as Fast, Dynamic, and High Motion.
- **One-click track and solve:** Runs detection, tracking, solve setup, solve, and refine from one command.
- **Auto scene setup:** Creates or reuses the scene camera, Camera Solver constraint, and clip background after solving.
- **Automatic filtering:** Removes short, duplicate, unstable, drifting, and solve-outlier tracks.
- **Occlusion and edge guards:** Reduces background tracks jumping onto foreground silhouettes and rejects ambiguous line-like points.
- **Motion consistency filtering:** Rejects jittery or locally inconsistent generated tracks before baking.
- **Balanced coverage:** Keeps markers distributed across the image instead of clustering in one area.
- **Mask-aware tracking:** Avoids masked regions and stops tracks that enter a forbidden area.
- **Cached add-more tracking:** Adds more 2D tracks from cached OpenCV candidates without rerunning the full analysis.
- **Blender-native output:** Writes normal Movie Clip Editor tracks named `AT_0001`, `AT_0002`, and so on.

## Recommended Footage

CV Auto Track works best on footage with visible texture and real camera motion.

- Camera tracking shots with stable environments
- Architecture, streets, interiors, and other corner-rich environments
- Dolly, drone, handheld, and pan shots with visible parallax
- Long or changing-view shots using the `Dynamic` preset
- Fast camera moves using the `High Motion` preset

## Difficult Footage

Some shots may need masks, a different preset, or manual cleanup.

- Heavy motion blur or defocus
- Large foreground occluders
- Unmasked people, vehicles, or other moving objects
- Reflections, transparent surfaces, repeated patterns, water, smoke, foliage, or sky
- Very low-texture walls or flat surfaces

## Current Workflow

1. Open footage in Blender's **Movie Clip Editor** and configure the clip settings as usual.
2. Open the Toolbar tab named `CV  Auto Track`.
3. Choose a tracking preset such as `Fast`, `Dynamic`, or `High Motion`.
4. *(Optional)* Open `Solve Setup` to review keyframes, tripod motion, Auto Scene Setup, focal length, distortion refinement, and Full Auto refine passes.
5. *(Optional)* Enable `Use Mask` to exclude moving objects or tracking-forbidden areas.
6. *(Optional)* Adjust `Density` only if you want fewer or more generated markers.
7. Click **`Run Auto Track`**.
8. Review the generated tracks and camera solve.
9. If necessary, remove poor tracks manually, adjust the solve, or add manual tracks using Blender's standard tracking workflow.
10. Run `Solve` or `Solve & Refine` again after any adjustments.

For a track-only pass without solving, use `Generate Tracks`.

> - `Generate Tracks` runs before a camera solve and therefore cannot use Bundle Error. After solving, `Run Auto Track` and `Solve & Refine` prioritize tracks whose elevated Bundle Error is supported by motion or geometry inconsistency, while very high Bundle Error can still be rejected on its own.
> - Running `Generate Tracks` first lets you remove tracks on moving objects or other problem areas before solving, which may improve quality without requiring a mask.
> - **CV Auto Track is designed to work together with Blender's standard tracking tools.** Add manual tracks whenever needed and continue with Blender's normal tracking workflow for difficult shots.
> - **Proxy Fallback:** For footage formats OpenCV cannot read directly, such as OpenEXR, CV Auto Track can build and use a Blender 100% proxy or reuse an existing one. A high proxy `Quality` setting is recommended to avoid reducing tracking accuracy.
> - **Protected Tracks:** Selected tracks are excluded from `Solve & Refine` filtering so important tracks can be preserved.

## Main Commands

- **Run Auto Track:** Detects features, tracks them, filters candidates, bakes Blender markers, optionally sets Keyframe A/B, runs Blender's camera solve, and runs solve refinement.
- **Generate Tracks:** Runs only detection, tracking, filtering, distribution, and marker baking.
  - **+ Add Tracks:** Adds a modest number of extra 2D tracks from the latest cached OpenCV candidates. It is enabled only after a compatible tracking pass.
- **Density:** Scales the generated marker amount. Lower values make a lighter solve set; higher values create denser coverage.
- **Solve Setup:** Opens common camera solve options in one dialog, including Auto Keyframe A/B, Auto Scene Setup, tripod motion, camera focal settings, distortion refinement, Full Auto refine passes, and baked marker area size.
  - **Auto Keyframe A/B:** Chooses stable solve keyframes automatically and disables Blender's built-in Keyframe Selection to avoid overlapping behavior.
  - **Auto Scene Setup:** Prepares the active scene camera after solving, including Camera Solver, clip background, and undistorted display when needed.
  - **Full Auto Refine Passes:** Sets how many solve-refine passes `Run Auto Track` may run.
  - **Bake Marker Size:** Sets the Pattern and Search area sizes for generated Blender markers.
- **Solve:** Runs Blender's standard camera solve from the add-on UI.
- **Solve & Refine:** Solves and removes tracks with very high Bundle Error or elevated Bundle Error corroborated by motion or geometry inconsistency.
- **Analyze Solve:** Selects or reports likely solve outliers without changing the solve by itself.
- **Delete Auto Tracks:** Deletes CV Auto Track-created `AT_` tracks from the active Movie Clip.

During Forward and Auto tracking, detection and tracking run in chunks so progress and cancellation remain responsive. Blender's solve and refine calls are still Blender operations and may pause the UI while they run.

If Radial Distortion refinement is enabled, CV Auto Track resets distortion values before solve/refine so the solve starts from a clean distortion state.

## Presets

- **Fast:** Fast general-purpose preset. Good first choice for normal footage.
- **Dynamic:** Better for long shots, pans, dolly moves, or shots where the view changes significantly.
- **High Motion:** Better for fast camera moves, rapid pans, or larger per-frame motion.
- **Balanced:** General-purpose preset with more analysis detail than Fast.
- **Sensitive:** More permissive detection for low-contrast or weak-texture footage.
- **Detailed:** Denser full-resolution analysis for slower but more thorough tracking.

Preset selection applies settings immediately. There is no separate Apply button.

The header preset menu uses Blender's standard preset system. Use it to save and reuse your own CV Auto Track settings. MovieClip and Mask datablock pointers are not stored in these presets.

## Filter Presets

- **Lenient:** Keeps more tracks and uses softer rejection. Useful for difficult footage or low coverage.
- **Standard:** Default general-purpose cleanup and refine balance.
- **Strict:** Rejects more aggressively when footage has dense, stable coverage.

Filter presets affect candidate cleanup and solve-refine rejection. They do not change tracking direction or detection density.

## Tracking Direction

- **Forward:** Tracks from the first frame of the selected range toward the end. Fastest mode.
- **Backward:** Tracks from the last frame of the selected range toward the beginning.
- **Both:** Tracks from the range center toward both ends.
- **Current:** Uses the current clip frame as the anchor and tracks both directions.
- **Auto:** Runs separate forward and backward passes. This is the default because it improves coverage on changing shots with little extra cost in typical use.

Backward, Both, Current, and Blender solve/refine stages can delay UI response more than Forward and Auto tracking.

## Track Setup

Track Setup controls which frames are analyzed and how OpenCV reads the footage before tracking.

- **Frame Range:** Chooses the clip range to process. Use Clip Full Range for most shots, or Custom Range when testing a shorter section.
- **Direction:** Chooses the tracking pass direction. `Auto` is the default for broader coverage; `Forward` is the fastest.
- **Analysis Scale:** Sets the temporary OpenCV analysis resolution. Lower values are faster; higher values can find more detail.
- **Use Mask:** Enables mask-aware detection and tracking. Use this when moving objects or forbidden areas should be avoided.

Advanced Mode adds minimum analysis resolution, frame cache size, Appearance Check, Edge Ambiguity, Silhouette Proximity, acceleration, and local-motion controls.

If OpenCV cannot read the source footage, CV Auto Track can use Blender's 100% proxy. Standard and custom proxy directories are supported.

## Track Modes

`Mode` controls how existing CV Auto Track markers are handled.

- **Auto Reuse:** Default. Generate Tracks replaces existing `AT_` tracks; Run Auto Track reuses existing `AT_` tracks and skips detection.
- **Replace:** Deletes existing `AT_` tracks before generating new ones.
- **Add New:** Keeps existing `AT_` tracks and adds another generated set.

## Masks

Enable `Use Mask` when moving objects or forbidden regions should be avoided.

Mask sources:

- **Blender Mask:** Uses the active Clip Editor mask or a selected Blender Mask datablock.
- **External MovieClip:** Uses a black/white or alpha mask loaded as a Blender MovieClip.

Mask modes:

- **White Area to Exclude:** White pixels are forbidden.
- **White Area to Track:** White pixels are allowed.

Mask handling applies to both detection and tracking. If a track enters the forbidden mask area or crosses a mask boundary, CV Auto Track ends that track, similar to how a track ends at the frame edge.

External mask clips are matched to the active footage at runtime. If the mask duration differs from the active clip, the UI shows a warning.

If the Movie Clip Editor is still in Mask mode after drawing a mask, CV Auto Track switches it back to Tracking mode before commands that create, solve, refine, or delete tracks.

## Baked Track Details

CV Auto Track writes normal Blender Movie Tracking markers. Generated tracks can be selected, hidden, edited, solved, or deleted with standard Movie Clip Editor tools.

Untracked ranges are baked as disabled marker spans, so `Viewport Overlays` > `Show Disabled` can hide inactive ranges cleanly.

Each baked marker receives Pattern and Search areas, keeping Blender's marker preview usable after baking.

The status line reports the final button-to-completion time, for example `Completed in 5.61s, 294 tracks`.

## Advanced Mode

Advanced Mode exposes lower-level controls for difficult footage and testing.

- **Track Setup:** Frame range, direction, analysis scale, cache size, and mask settings.
- **Distribution:** Grid and coverage behavior for marker placement.
- **Detection:** OpenCV detector settings such as maximum features, quality, spacing, block size, and edge margin.
- **Tracking:** Lucas-Kanade optical-flow settings, appearance consistency, and edge/silhouette guards.
- **Filtering:** Length, duplicate, validity, acceleration, local-motion, and multi-baseline RANSAC thresholds.
- **Refine Settings:** Bundle Error, motion/geometry evidence, protection options, and outlier behavior.
- **Existing Tracks:** Controls how user-created and existing `AT_` tracks are protected or reused.

`Auto Scale Pixel Parameters` is enabled by default. Pixel-based settings are scaled internally from the effective analysis resolution so presets behave more consistently across FHD, 4K, and different Analysis Scale choices.

Experimental detector options such as `SIFT`, `ORB`, and `FAST` are available in Advanced Mode. `Shi-Tomasi` remains the default and is usually the best fit for fast Lucas-Kanade tracking.

Multi-baseline RANSAC is enabled by default and rejects a track only after repeated disagreement across several valid frame pairs.

---

# Frequently Asked Questions

- **The camera solve is incorrect or unstable.**  
  Make sure your camera settings are correct before solving. If **Auto Keyframe A/B** selects unsuitable keyframes, disable it and manually choose different **Keyframe A** and **Keyframe B** values, then solve again.

- **Focal Length or Radial Distortion is not estimated correctly.**  
  CV Auto Track uses Blender's built-in camera solver for camera calibration. Depending on the footage, selected keyframes, and initial camera parameters, these values may not be estimated accurately. Try different keyframes or more suitable initial values.

- **Too many good tracks are removed.**  
  **Run Auto Track** and **Solve & Refine** remove solve outliers according to the selected **Filter** preset. To keep all generated tracks, use **Generate Tracks** followed by **Solve**, adjust the Filter settings, or select important tracks to protect them from Refine filtering.

- **Processing is very slow.**  
  Processing time increases with source resolution, Density, clip length, and the `Detailed` preset. As a reference, a 200-frame Full HD clip typically finishes in under 20 seconds with the `Fast` preset, depending on hardware.

- **No tracks are generated with any preset.**  
  The footage may not be suitable for automatic tracking. CV Auto Track works best with visible texture, real camera motion, good image quality, stable lighting, and limited motion blur or defocus.

## License

CV Auto Track is GPL-3.0-or-later. OpenCV is Apache-2.0 licensed.
