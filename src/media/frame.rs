use std::sync::atomic::{AtomicU64, Ordering};

use super::ffmpeg::{self, format::Pixel, software::scaling, util::color};

/// YCbCr to RGB conversion matrix.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Matrix {
    Bt601,
    Bt709,
    Bt2020,
}

/// A decoded picture in planar 8-bit YUV 4:2:0, ready to upload as three
/// single-channel textures. Planes are tightly packed (stride == width).
#[derive(Debug)]
pub struct VideoFrame {
    /// Unique id, so the renderer uploads each frame exactly once.
    pub id: u64,
    /// Media time in seconds.
    pub time: f64,
    pub width: u32,
    pub height: u32,
    pub y: Vec<u8>,
    pub u: Vec<u8>,
    pub v: Vec<u8>,
    pub matrix: Matrix,
    pub full_range: bool,
}

impl VideoFrame {
    pub fn chroma_size(&self) -> (u32, u32) {
        (self.width.div_ceil(2), self.height.div_ceil(2))
    }

    pub fn bytes(&self) -> usize {
        self.y.len() + self.u.len() + self.v.len()
    }
}

// SAFETY: the scaling context is only ever used by the thread that owns the
// converter; it is moved between threads, never shared.
unsafe impl Send for FrameConverter {}

fn next_id() -> u64 {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    NEXT.fetch_add(1, Ordering::Relaxed)
}

/// Turns decoder output of any pixel format into [`VideoFrame`]s.
#[derive(Default)]
pub struct FrameConverter {
    scaler: Option<(Pixel, u32, u32, scaling::Context)>,
    scratch: Option<ffmpeg::frame::Video>,
}

impl FrameConverter {
    pub fn convert(
        &mut self,
        src: &ffmpeg::frame::Video,
        time: f64,
    ) -> Result<VideoFrame, ffmpeg::Error> {
        let (w, h) = (src.width(), src.height());
        let full_range = src.color_range() == color::Range::JPEG
            || matches!(
                src.format(),
                Pixel::YUVJ420P | Pixel::YUVJ422P | Pixel::YUVJ444P
            );
        let matrix = match src.color_space() {
            color::Space::BT709 => Matrix::Bt709,
            color::Space::BT2020NCL | color::Space::BT2020CL => Matrix::Bt2020,
            color::Space::BT470BG | color::Space::SMPTE170M | color::Space::FCC => Matrix::Bt601,
            _ if h >= 720 => Matrix::Bt709,
            _ => Matrix::Bt601,
        };

        let planar = if matches!(src.format(), Pixel::YUV420P | Pixel::YUVJ420P) {
            src
        } else {
            self.convert_to_yuv420p(src)?
        };

        let (cw, ch) = (w.div_ceil(2) as usize, h.div_ceil(2) as usize);
        Ok(VideoFrame {
            id: next_id(),
            time,
            width: w,
            height: h,
            y: pack_plane(planar, 0, w as usize, h as usize),
            u: pack_plane(planar, 1, cw, ch),
            v: pack_plane(planar, 2, cw, ch),
            matrix,
            full_range,
        })
    }

    fn convert_to_yuv420p<'a>(
        &'a mut self,
        src: &ffmpeg::frame::Video,
    ) -> Result<&'a ffmpeg::frame::Video, ffmpeg::Error> {
        let key = (src.format(), src.width(), src.height());
        if self
            .scaler
            .as_ref()
            .is_none_or(|(f, w, h, _)| (*f, *w, *h) != key)
        {
            let ctx = scaling::Context::get(
                key.0,
                key.1,
                key.2,
                Pixel::YUV420P,
                key.1,
                key.2,
                scaling::Flags::BILINEAR,
            )?;
            self.scaler = Some((key.0, key.1, key.2, ctx));
            self.scratch = None;
        }
        let (_, _, _, scaler) = self.scaler.as_mut().expect("scaler just set");
        let out = self.scratch.get_or_insert_with(ffmpeg::frame::Video::empty);
        scaler.run(src, out)?;
        Ok(out)
    }
}

fn pack_plane(frame: &ffmpeg::frame::Video, plane: usize, width: usize, height: usize) -> Vec<u8> {
    let stride = frame.stride(plane);
    let data = frame.data(plane);
    if stride == width {
        return data[..width * height].to_vec();
    }
    let mut out = Vec::with_capacity(width * height);
    for row in data.chunks(stride).take(height) {
        out.extend_from_slice(&row[..width]);
    }
    out
}
