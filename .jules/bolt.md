## 2024-06-25 - MoviePy Cropping Optimization
**Learning:** In MoviePy, when converting video aspect ratios (e.g. 16:9 to 9:16), it is significantly faster to crop the original clip to the target aspect ratio *before* resizing, rather than resizing the entire clip to the target height/width and then cropping the excess. Resizing the whole frame processes millions of unnecessary pixels.
**Action:** When reframing video using MoviePy, always crop the dimensions based on aspect ratio first, then apply the resize operation to the smaller cropped region.

## 2024-08-16 - MoviePy TextClip ImageMagick Overhead
**Learning:** Instantiating `TextClip` objects in MoviePy is surprisingly slow because it spawns an external ImageMagick (`convert`) process to render each piece of text. For karaoke-style captions where words repeat frequently (e.g., "the", "and"), generating a new `TextClip` for every instance creates a massive performance bottleneck.
**Action:** Always cache and reuse base `TextClip` objects for identical strings. MoviePy's chainable modifier methods like `.set_start()`, `.set_duration()`, and `.set_position()` safely return lightweight copies, making it safe to mutate the timing/position of a single cached text render.

## 2024-08-17 - MoviePy VideoFileClip Initialization Overhead
**Learning:** Instantiating a `VideoFileClip` in `moviepy` is an expensive operation because it spawns an `ffmpeg` subprocess to read the video metadata (like duration, resolution, etc.). If a script needs multiple properties of a video (e.g., in a validation step), opening the clip multiple times multiplies this overhead. Additionally, passing `audio=False` skips parsing the audio stream entirely, speeding up the parsing.
**Action:** Always batch video property extraction by instantiating `VideoFileClip` exactly once. If audio properties aren't needed, pass `audio=False` to avoid unnecessary I/O.
