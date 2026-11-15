// Planar YUV 4:2:0 to RGB, drawn as a full-viewport triangle.

struct Params {
    // Rows of the YCbCr->RGB matrix; w is unused.
    r: vec4<f32>,
    g: vec4<f32>,
    b: vec4<f32>,
    // x: luma offset, y: luma scale, z: chroma scale, w: 1 if target is sRGB.
    range: vec4<f32>,
};

@group(0) @binding(0) var tex_y: texture_2d<f32>;
@group(0) @binding(1) var tex_u: texture_2d<f32>;
@group(0) @binding(2) var tex_v: texture_2d<f32>;
@group(0) @binding(3) var samp: sampler;
@group(0) @binding(4) var<uniform> params: Params;

struct VsOut {
    @builtin(position) pos: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) i: u32) -> VsOut {
    let xy = vec2<f32>(f32((i << 1u) & 2u), f32(i & 2u));
    var out: VsOut;
    out.pos = vec4<f32>(xy * 2.0 - 1.0, 0.0, 1.0);
    out.uv = vec2<f32>(xy.x, 1.0 - xy.y);
    return out;
}

fn srgb_to_linear(c: vec3<f32>) -> vec3<f32> {
    let lo = c / 12.92;
    let hi = pow((c + 0.055) / 1.055, vec3<f32>(2.4));
    return select(hi, lo, c <= vec3<f32>(0.04045));
}

@fragment
fn fs_main(in: VsOut) -> @location(0) vec4<f32> {
    let y = (textureSample(tex_y, samp, in.uv).r - params.range.x) * params.range.y;
    let u = (textureSample(tex_u, samp, in.uv).r - 0.5019608) * params.range.z;
    let v = (textureSample(tex_v, samp, in.uv).r - 0.5019608) * params.range.z;
    let yuv = vec3<f32>(y, u, v);
    var rgb = clamp(vec3<f32>(dot(params.r.xyz, yuv), dot(params.g.xyz, yuv), dot(params.b.xyz, yuv)), vec3<f32>(0.0), vec3<f32>(1.0));
    if (params.range.w > 0.5) {
        rgb = srgb_to_linear(rgb);
    }
    return vec4<f32>(rgb, 1.0);
}
