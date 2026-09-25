# Background Music

Create one subfolder per genre and drop royalty-free MP3s (from YouTube Audio Library or
Pixabay Music — both explicitly free for monetized YouTube use) here:

```
music/
├── horror/
├── mystery/
├── thriller/
├── emotional/
└── motivational/
```

`make_video.py` picks a random track from the matching genre folder for each story and
ducks it to ~10% volume under the voice automatically (sidechain compression) — no manual
mixing needed.
