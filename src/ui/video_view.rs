//! Draws [`VideoFrame`]s on the GPU: three plane textures and a YUV->RGB
//! shader, painted as an egui callback so overlays composite on top.

use std::sync::Arc;

use eframe::egui;
use eframe::egui_wgpu::{self, CallbackResources, CallbackTrait, ScreenDescriptor, wgpu};

use crate::media::VideoFrame;
use crate::media::frame::Matrix;

struct Planes {
    size: (u32, u32),
    textures: [wgpu::Texture; 3],
    bind_group: wgpu::BindGroup,
}

pub struct VideoRenderer {
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    params: wgpu::Buffer,
    srgb_target: bool,
    planes: Option<Planes>,
    uploaded: u64,
}

/// Register the renderer with eframe's wgpu state. Returns false if the app
/// is not running on wgpu.
pub fn install(cc: &eframe::CreationContext<'_>) -> bool {
    let Some(rs) = cc.wgpu_render_state.as_ref() else {
        return false;
    };
    let renderer = VideoRenderer::new(&rs.device, rs.target_format);
    rs.renderer.write().callback_resources.insert(renderer);
    true
}

/// Paint `frame` into `rect` (already letterboxed by the caller).
pub fn paint(painter: &egui::Painter, rect: egui::Rect, frame: Arc<VideoFrame>) {
    painter.add(egui_wgpu::Callback::new_paint_callback(
        rect,
        VideoCallback(frame),
    ));
}

struct VideoCallback(Arc<VideoFrame>);

impl CallbackTrait for VideoCallback {
    fn prepare(
        &self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        _screen: &ScreenDescriptor,
        _encoder: &mut wgpu::CommandEncoder,
        resources: &mut CallbackResources,
    ) -> Vec<wgpu::CommandBuffer> {
        if let Some(r) = resources.get_mut::<VideoRenderer>() {
            r.upload(device, queue, &self.0);
        }
        Vec::new()
    }

    fn paint(
        &self,
        _info: egui::PaintCallbackInfo,
        pass: &mut wgpu::RenderPass<'static>,
        resources: &CallbackResources,
    ) {
        let Some(r) = resources.get::<VideoRenderer>() else {
            return;
        };
        let Some(planes) = &r.planes else { return };
        pass.set_pipeline(&r.pipeline);
        pass.set_bind_group(0, &planes.bind_group, &[]);
        pass.draw(0..3, 0..1);
    }
}

impl VideoRenderer {
    fn new(device: &wgpu::Device, target: wgpu::TextureFormat) -> Self {
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("video"),
            source: wgpu::ShaderSource::Wgsl(include_str!("video.wgsl").into()),
        });
        let tex_entry = |binding| wgpu::BindGroupLayoutEntry {
            binding,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Texture {
                sample_type: wgpu::TextureSampleType::Float { filterable: true },
                view_dimension: wgpu::TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        };
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("video"),
            entries: &[
                tex_entry(0),
                tex_entry(1),
                tex_entry(2),
                wgpu::BindGroupLayoutEntry {
                    binding: 3,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 4,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
            ],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("video"),
            bind_group_layouts: &[Some(&layout)],
            immediate_size: 0,
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("video"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_main"),
                compilation_options: wgpu::PipelineCompilationOptions::default(),
                buffers: &[],
            },
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_main"),
                compilation_options: wgpu::PipelineCompilationOptions::default(),
                targets: &[Some(wgpu::ColorTargetState {
                    format: target,
                    blend: None,
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            multiview_mask: None,
            cache: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("video"),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let params = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("video params"),
            size: 64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        Self {
            pipeline,
            layout,
            sampler,
            params,
            srgb_target: target.is_srgb(),
            planes: None,
            uploaded: 0,
        }
    }

    fn upload(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, frame: &VideoFrame) {
        if self.uploaded == frame.id {
            return;
        }
        self.uploaded = frame.id;
        let size = (frame.width, frame.height);
        if self.planes.as_ref().is_none_or(|p| p.size != size) {
            self.planes = Some(self.create_planes(device, frame));
        }
        let planes = self.planes.as_ref().expect("planes just created");
        let (cw, ch) = frame.chroma_size();
        let dims = [(frame.width, frame.height), (cw, ch), (cw, ch)];
        for ((texture, data), (w, h)) in planes
            .textures
            .iter()
            .zip([&frame.y, &frame.u, &frame.v])
            .zip(dims)
        {
            queue.write_texture(
                wgpu::TexelCopyTextureInfo {
                    texture,
                    mip_level: 0,
                    origin: wgpu::Origin3d::ZERO,
                    aspect: wgpu::TextureAspect::All,
                },
                data,
                wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(w),
                    rows_per_image: Some(h),
                },
                wgpu::Extent3d {
                    width: w,
                    height: h,
                    depth_or_array_layers: 1,
                },
            );
        }
        queue.write_buffer(
            &self.params,
            0,
            &params_bytes(frame.matrix, frame.full_range, self.srgb_target),
        );
    }

    fn create_planes(&self, device: &wgpu::Device, frame: &VideoFrame) -> Planes {
        let (cw, ch) = frame.chroma_size();
        let make = |w: u32, h: u32| {
            device.create_texture(&wgpu::TextureDescriptor {
                label: Some("video plane"),
                size: wgpu::Extent3d {
                    width: w,
                    height: h,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: wgpu::TextureFormat::R8Unorm,
                usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
                view_formats: &[],
            })
        };
        let textures = [make(frame.width, frame.height), make(cw, ch), make(cw, ch)];
        let views: Vec<_> = textures
            .iter()
            .map(|t| t.create_view(&wgpu::TextureViewDescriptor::default()))
            .collect();
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("video"),
            layout: &self.layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(&views[0]),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::TextureView(&views[1]),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: wgpu::BindingResource::TextureView(&views[2]),
                },
                wgpu::BindGroupEntry {
                    binding: 3,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
                wgpu::BindGroupEntry {
                    binding: 4,
                    resource: self.params.as_entire_binding(),
                },
            ],
        });
        Planes {
            size: (frame.width, frame.height),
            textures,
            bind_group,
        }
    }
}

/// Uniform block for the shader: matrix rows, then range parameters.
fn params_bytes(matrix: Matrix, full_range: bool, srgb_target: bool) -> [u8; 64] {
    let (kr, kb) = match matrix {
        Matrix::Bt601 => (0.299, 0.114),
        Matrix::Bt709 => (0.2126, 0.0722),
        Matrix::Bt2020 => (0.2627, 0.0593),
    };
    let kg = 1.0 - kr - kb;
    let rows = [
        [1.0, 0.0, 2.0 * (1.0 - kr), 0.0],
        [
            1.0,
            -2.0 * kb * (1.0 - kb) / kg,
            -2.0 * kr * (1.0 - kr) / kg,
            0.0,
        ],
        [1.0, 2.0 * (1.0 - kb), 0.0, 0.0],
    ];
    let range = if full_range {
        [0.0, 1.0, 1.0, 0.0]
    } else {
        [16.0 / 255.0, 255.0 / 219.0, 255.0 / 224.0, 0.0]
    };
    let mut values = [0.0f32; 16];
    for (i, row) in rows.iter().enumerate() {
        values[i * 4..i * 4 + 4].copy_from_slice(row);
    }
    values[12..16].copy_from_slice(&range);
    values[15] = if srgb_target { 1.0 } else { 0.0 };
    let mut out = [0u8; 64];
    for (chunk, v) in out.as_chunks_mut::<4>().0.iter_mut().zip(values) {
        *chunk = v.to_ne_bytes();
    }
    out
}
