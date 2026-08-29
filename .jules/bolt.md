## 2024-06-25 - MoviePy Cropping Optimization
**Learning:** In MoviePy, when converting video aspect ratios (e.g. 16:9 to 9:16), it is significantly faster to crop the original clip to the target aspect ratio *before* resizing, rather than resizing the entire clip to the target height/width and then cropping the excess. Resizing the whole frame processes millions of unnecessary pixels.
**Action:** When reframing video using MoviePy, always crop the dimensions based on aspect ratio first, then apply the resize operation to the smaller cropped region.

## 2024-08-16 - MoviePy TextClip ImageMagick Overhead
**Learning:** Instantiating `TextClip` objects in MoviePy is surprisingly slow because it spawns an external ImageMagick (`convert`) process to render each piece of text. For karaoke-style captions where words repeat frequently (e.g., "the", "and"), generating a new `TextClip` for every instance creates a massive performance bottleneck.
**Action:** Always cache and reuse base `TextClip` objects for identical strings. MoviePy's chainable modifier methods like `.set_start()`, `.set_duration()`, and `.set_position()` safely return lightweight copies, making it safe to mutate the timing/position of a single cached text render.

## 2024-09-04 - MoviePy VideoFileClip Instantiation Overhead
**Learning:** Instantiating `moviepy.editor.VideoFileClip` is expensive due to underlying `ffprobe` subprocess calls. When multiple validation checks (e.g., duration and resolution) inspect the same video file, opening the file multiple times creates redundant parsing overhead.
**Action:** Cache `VideoFileClip` properties (like duration, width, height) instead of reopening the file for each check to prevent redundant `ffprobe` calls and speed up validation.
