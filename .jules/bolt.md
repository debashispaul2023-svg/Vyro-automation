## 2024-06-25 - MoviePy Cropping Optimization
**Learning:** In MoviePy, when converting video aspect ratios (e.g. 16:9 to 9:16), it is significantly faster to crop the original clip to the target aspect ratio *before* resizing, rather than resizing the entire clip to the target height/width and then cropping the excess. Resizing the whole frame processes millions of unnecessary pixels.
**Action:** When reframing video using MoviePy, always crop the dimensions based on aspect ratio first, then apply the resize operation to the smaller cropped region.

## 2024-08-16 - MoviePy TextClip ImageMagick Overhead
**Learning:** Instantiating `TextClip` objects in MoviePy is surprisingly slow because it spawns an external ImageMagick (`convert`) process to render each piece of text. For karaoke-style captions where words repeat frequently (e.g., "the", "and"), generating a new `TextClip` for every instance creates a massive performance bottleneck.
**Action:** Always cache and reuse base `TextClip` objects for identical strings. MoviePy's chainable modifier methods like `.set_start()`, `.set_duration()`, and `.set_position()` safely return lightweight copies, making it safe to mutate the timing/position of a single cached text render.

## 2024-08-20 - MoviePy VideoFileClip Redundant Instantiation Overhead
**Learning:** Instantiating `VideoFileClip` in moviepy requires reading video headers and spawning an ffmpeg probe process underneath. Calling it multiple times for the same file in a validation loop (e.g. once for duration, once for resolution) doubles the I/O cost and severely hurts performance, especially in blocking contexts.
**Action:** When validating multiple properties of a video file, instantiate the `VideoFileClip` exactly once, extract all necessary properties (e.g. `duration`, `w`, `h`), close the clip, and then pass those properties to independent validation functions.
