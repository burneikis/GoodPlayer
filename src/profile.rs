//! The two editions built from this crate. DoesPlayer is GoodPlayer with
//! the extras switched off: same engine, smaller surface.

#[derive(Debug)]
pub struct Profile {
    pub name: &'static str,
    /// Desktop/WM application id and settings directory name.
    pub app_id: &'static str,
    pub icon_png: &'static [u8],
    /// Timeline hover previews and the thumbnail strip.
    pub thumbnails: bool,
    /// "Frame N / M" overlay.
    pub frame_counter: bool,
    /// Decoded-frame cache budget; bigger means longer instant back-steps.
    pub cache_mb: usize,
}

pub const GOODPLAYER: Profile = Profile {
    name: "GoodPlayer",
    app_id: "goodplayer",
    icon_png: include_bytes!("../assets/goodplayer.png"),
    thumbnails: true,
    frame_counter: true,
    cache_mb: 512,
};

pub const DOESPLAYER: Profile = Profile {
    name: "DoesPlayer",
    app_id: "doesplayer",
    icon_png: include_bytes!("../assets/doesplayer.png"),
    thumbnails: false,
    frame_counter: false,
    cache_mb: 192,
};
