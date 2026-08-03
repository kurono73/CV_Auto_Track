# CV Auto Track
## Overview

CV Auto Track adds fast OpenCV-powered auto tracking to Blender's Movie Clip Editor.

It is designed for a simple preset-based workflow: open footage, choose a preset, and run automatic tracking. The add-on detects 2D feature points, tracks them through the clip, filters weak candidates, balances screen coverage, and bakes the result as standard Blender Movie Tracking markers.

## Features:

- **Fast OpenCV auto tracking:** Generates many 2D tracking markers quickly.
- **Simple presets:** Start from footage-oriented presets such as Fast, Dynamic, and High Motion.
- **One-click track and solve:** Run detection, tracking, solve setup, solve, and refine from one command.
- **Automatic filtering:** Removes short, duplicate, unstable, or solve-outlier tracks.
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
- Codecs that Blender can read but OpenCV cannot read reliably

## Current Workflow

1. Open footage in Blender's Movie Clip Editor and set the footage settings as usual.
2. Open the Toolbar tab named `CV  Auto Track`.
3. Choose a tracking preset such as `Fast`, `Dynamic`, or `High Motion`.
4. Optional: open `Solve Setup` to review keyframes, tripod motion, focal length, distortion refine options, and Full Auto refine passes.
5. Optional: enable `Use Mask` to avoid moving objects or tracking-forbidden areas.
6. Adjust `Density` only when you want fewer or more generated markers.
7. Click `Run Auto Track`.
8. Review the result. If needed, run `Solve` or `Solve & Refine` again after adjustment.

For a track-only pass without solving, use `Generate Tracks`.

## Main Commands

- **Run Auto Track:** Detects features, tracks them, filters candidates, bakes Blender markers, optionally sets Keyframe A/B, runs Blender's camera solve, and runs solve refinement.
- **Generate Tracks:** Runs only detection, tracking, filtering, distribution, and marker baking.
  - **+ Add Tracks:** Adds a modest number of extra 2D tracks from the latest cached OpenCV candidates. It is enabled only after a compatible tracking pass.
- **Density:** Scales the generated marker amount. Lower values make a lighter solve set; higher values create denser coverage.
- **Solve Setup:** Opens common camera solve options in one dialog, including Auto Keyframe A/B, tripod motion, camera focal settings, distortion refine options, Full Auto refine passes, and baked marker area size.
  - **Auto Keyframe A/B:** Chooses stable solve keyframes automatically and disables Blender's built-in Keyframe Selection to avoid overlapping behavior.
  - **Full Auto Refine Passes:** Sets how many solve-refine passes Run Auto Track may run.
  - **Bake Marker Size:** Sets the Pattern and Search area size for generated Blender markers.

- **Solve:** Runs Blender's standard camera solve from the add-on UI.
- **Solve & Refine:** Runs solve and removes high-error or motion-inconsistent tracks in controlled passes.
- **Analyze Solve:** Selects or reports likely solve outliers without changing the solve by itself.
- **Restore Previous State:** Deletes CV Auto Track-created `AT_` tracks from the active Movie Clip.

During Forward and Auto tracking, the detection/tracking stage runs in chunks so progress and cancellation remain responsive. Blender's solve and refine calls are still Blender operations and may pause the UI while they run.

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

Advanced Mode adds minimum analysis resolution, frame cache size, and other technical controls.


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

External mask clips can be synced to the active footage settings. If the mask duration differs from the active clip, the UI shows a warning.

If radial distortion refine is enabled, CV Auto Track resets distortion values before solve/refine so the solve starts from a clean distortion state.

## Baked Track Details

CV Auto Track writes normal Blender Movie Tracking markers. The generated tracks can be selected, hidden, edited, solved, or deleted with standard Movie Clip Editor tools.

Untracked ranges are baked as disabled marker spans, so `Viewport Overlays`>`Show Disabled` display option can hide inactive ranges cleanly.

The status line reports the final button-to-completion time, for example `Completed in 5.61s, 294 tracks`.

## Advanced Mode

Advanced Mode exposes lower-level controls for difficult footage and testing.

- **Track Setup:** Frame range, direction, analysis scale, cache size, and mask settings.
- **Distribution:** Grid and coverage behavior for marker placement.
- **Detection:** OpenCV detector settings such as maximum features, quality, spacing, block size, and edge margin.
- **Tracking:** Lucas-Kanade optical-flow settings such as window size, pyramid levels, motion limit, and forward-backward check.
- **Filtering:** Length, duplicate, validity, and RANSAC-related cleanup thresholds.
- **Refine Settings:** Solve-refine thresholds, protection options, and outlier behavior.
- **Existing Tracks:** Controls how user-created and existing `AT_` tracks are protected or reused.

`Auto Scale Pixel Parameters` is enabled by default. Pixel-based settings are scaled internally from the effective analysis resolution so presets behave more consistently across FHD, 4K, and different analysis-scale choices.
  
Experimental detector options such as `SIFT`, `ORB`, and `FAST` are available in Advanced Mode. `Shi-Tomasi` remains the default and is usually the best fit for fast Lucas-Kanade tracking.

---

# Frequently Asked Questions

- **The camera solve is incorrect or unstable.**  
    Make sure your camera settings are correct before solving.
    If **Auto Keyframe A/B** selects unsuitable keyframes, disable it and manually choose a different **Keyframe A** and **Keyframe B**, then solve again.  
    
- **Focal Length or Radial Distortion is not estimated correctly.**  
    CV Auto Track uses Blender's built-in camera solver for camera calibration.  
    Depending on the footage, the selected **Keyframe A/B**, or the initial camera parameters, **Focal Length** and **Radial Distortion** may not be estimated accurately.  
    Try selecting different keyframes or providing more suitable initial values.
    
- **Too many good tracks are removed.**  
    **Run Auto Track** and **Solve & Refine** automatically remove high-error tracks based on the selected **Filter** settings.  
    If you want to keep all generated tracks:  
    - Use **Generate Tracks** followed by Blender's standard **Solve**.
    - Or adjust the **Filter** settings before running the solve.
- **Processing is very slow.**
    Processing time depends on several factors, including:
    - High source resolution
    - Higher **Density** values
    - Long footage
    - Using the **Detailed** preset  
    As a reference, a **200-frame Full HD clip** typically finishes in **around 15 seconds** with the **Fast** preset, depending on your hardware.
    
- **The camera does not move in the 3D View after solving.**  
    After solving, follow Blender's standard camera tracking workflow.  
    You still need to:  
    - Apply the **Camera Solver** constraint.
    - Perform the camera layout/alignment for your scene.
    
- **No tracks are generated with any preset.**  
    Your footage may not be suitable for automatic tracking.  
    CV Auto Track works best on footage with:  
    - Visible texture and feature-rich surfaces
    - Real camera motion
    - Good image quality
    - Stable lighting
    - Limited motion blur and defocus
