# CapCut feature check

What CapCut offers, whether this editor can do it for free on your own computer, and where it stands.
Checked November 2026.

**How reliable the ranking is:** I found no official or independent usage data. CapCut's own help pages
list features but not how often they're used, and most "top features" articles are marketing blogs.
Auto captions, templates, background removal and beat sync are named most consistently. The rest of
the list comes from CapCut's menus and review sites, so treat the order as a rough guide.

## Most commonly recommended

| CapCut feature | Here | How |
|---|---|---|
| Auto captions | ✅ | Free local transcription, 76 styles |
| Templates | ✅ | 6 presets, including the new Viral (CapCut's huge template library is not copied) |
| Background removal | ✅ | Blur, colour, photo or video behind you. Slow; works best on a person facing the camera |
| Beat sync | ✅ | Pulse on the beat in the Studio; `beats.py snap` moves cuts onto beats |

## Cutting and speed

| CapCut feature | Here | How |
|---|---|---|
| Split, trim, delete, reorder | ✅ | Storyboard |
| Remove filler words and pauses | ✅ | Automatic |
| Edit by transcript | ✅ | Ask Claude, or use the storyboard's lines |
| Speed change | ✅ | Per section |
| Speed curves (Montage, Hero, Bullet, Jump cut, Flash in/out) | ✅ New | Section drawer |
| Smooth slow motion | ✅ New | `smooth` on a section; ask Claude |
| Reverse, freeze frame | ✅ | Reverse: ask Claude. Freeze: section drawer |
| Stabilize | ✅ | Section drawer |
| Long video to short clips | ⚠️ | Ask Claude; there's no button |

## Picture

| CapCut feature | Here | How |
|---|---|---|
| Crop, reframe to any shape | ✅ | Automatic, around the face |
| Smart reframe that follows the person | ✅ New | Automatic when they move |
| Canvas: whole shot on blurred or coloured sides | ✅ New | Section drawer |
| Flip, rotate | ✅ New | Flip: section drawer. Rotate: ask Claude |
| Filters / looks | ✅ | 35 looks, plus your own `.cube` files |
| Look on one section only | ✅ New | Section drawer |
| Adjust: brightness, contrast, saturation, warmth, tint, hue, sharpen, vignette, grain and more | ✅ New | Look tab, Adjust by hand |
| Colour match between clips | ✅ New | `color_match`; ask Claude |
| Skin smoothing | ✅ New | Smooth skin effect (whole frame, not face only) |
| Blur or pixelate a face or area | ✅ New | `region` on an effect; ask Claude. Doesn't follow a moving face |
| Video effects | ✅ | 38 effects |
| Transitions | ✅ | 21 transitions and 3 effect cuts |
| Fade in and out | ✅ New | Audio tab |
| Slow camera moves / Ken Burns | ✅ New | Section drawer |
| Keyframes | ✅ New | Zooms and overlays; ask Claude. No drag-handles in the Studio yet |
| Photos in a video | ⚠️ New | Works through Claude; the Home screen doesn't accept photos yet |

## Overlays and text

| CapCut feature | Here | How |
|---|---|---|
| Text with in, out and loop animations | ✅ New | Effects tab; more options through Claude |
| Stickers and emoji, animated | ✅ New | Effects tab; your own PNGs through Claude |
| Emoji added to captions automatically | ✅ New | Captions tab |
| Picture-in-picture and B-roll | ✅ | Ask Claude |
| Masks: circle, heart, star and more | ✅ New | Ask Claude |
| Blend modes | ✅ New | Ask Claude |
| Green screen / chroma key | ✅ New | Ask Claude |
| Motion graphics | ✅ | 14 templates |

## Sound

| CapCut feature | Here | How |
|---|---|---|
| Voice clean-up | ✅ | Audio tab |
| Voice changer | ✅ New | 12 effects, whole video or one section |
| Per-section volume and mute | ✅ New | Section drawer |
| Music with automatic ducking | ✅ | Your own tracks |
| Sound effects | ⚠️ | 5 built in, plus your own files |
| Text-to-speech | ⚠️ New | Uses the computer's built-in voice, which sounds more robotic than CapCut's. Through Claude |
| Vocal isolation | 🔜 | Possible with Demucs, a large free download. Needs your go-ahead |
| Translate captions | ⚠️ | Claude can translate them. No button yet |

## Not added

| CapCut feature | Why |
|---|---|
| Drag-and-drop multi-track timeline | Possible, but a big piece of work. The best next UI project |
| Licensed music and sound library | Licensing: those tracks can't be redistributed. Bring your own (`music/README.md` lists free sources) |
| Generative AI: text to video, avatars, AI images | Needs paid cloud services or far more computing power, so it breaks the free and local rule |
| AI dubbing and lip sync | Same reason |
| Body and face AR effects | Possible only in part with MediaPipe; low priority |
| 50,000-item template and effects marketplace | That's a content library, not a feature |
| Cloud storage and team sharing | Everything stays on your computer by design |

## Worth knowing

- **Many effects at once get slow.** Each effect processes the whole video, not just its moment. Five
  rendered in under a minute on a 20-second test; 24 had not finished after 15 minutes when stopped.
- **Fonts:** the new caption styles use fonts that ship with Windows. Other systems will substitute.

## Sources

- [CapCut: how to use CapCut on PC](https://www.capcut.com/resource/how-to-use-capcut-on-pc)
- [CapCut desktop video editor](https://capcut.com/tools/desktop-video-editor)
- [Filmora: CapCut for PC review](https://filmora.wondershare.com/video-editor-review/capcut-for-pc.html)
- [Free Academy: CapCut AI features review](https://freeacademy.ai/blog/capcut-ai-features-complete-guide-review-2026)
- [Best CapCut editing tricks for beginners](https://edit-with-zakir.blogspot.com/2026/05/best-capcut-editing-tricks-for.html)
