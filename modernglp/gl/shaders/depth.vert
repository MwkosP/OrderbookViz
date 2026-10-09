#version 330 core

in vec2 in_position;

uniform vec2 u_price_range;  // min, max
uniform float u_max_size;

void main() {
    float x = (in_position.x - u_price_range.x) / max(u_price_range.y - u_price_range.x, 1e-6);
    float y = in_position.y / max(u_max_size, 1e-6);

    // NDC with padding around the plot area
    float ndc_x = mix(-0.92, 0.92, clamp(x, 0.0, 1.0));
    float ndc_y = mix(-0.88, 0.88, clamp(y, 0.0, 1.0));
    gl_Position = vec4(ndc_x, ndc_y, 0.0, 1.0);
}
