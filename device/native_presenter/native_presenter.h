#ifndef NIMBUS_NATIVE_PRESENTER_H
#define NIMBUS_NATIVE_PRESENTER_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define NP_API __declspec(dllexport)
#else
#define NP_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct np_presenter np_presenter;

enum np_create_flags {
    NP_FULLSCREEN_DESKTOP = 1u << 0,
    NP_VSYNC = 1u << 1,
    NP_HIDDEN = 1u << 2,
    NP_REQUIRE_ACCELERATED = 1u << 3,
};

enum np_event_type {
    NP_EVENT_NONE = 0,
    NP_EVENT_QUIT = 1,
    NP_EVENT_POINTER_DOWN = 2,
    NP_EVENT_POINTER_UP = 3,
    NP_EVENT_KEY_DOWN = 4,
    NP_EVENT_KEY_UP = 5,
    NP_EVENT_FOCUS_LOST = 6,
};

typedef struct np_event {
    uint32_t type;
    float x;
    float y;
    float pressure;
    int32_t keycode;
    uint8_t repeat;
    uint8_t inside;
    uint8_t reserved[2];
} np_event;

typedef struct np_renderer_info {
    char renderer_name[64];
    char video_driver[64];
    uint32_t renderer_flags;
    int32_t texture_width;
    int32_t texture_height;
    int32_t output_width;
    int32_t output_height;
    uint8_t accelerated;
    uint8_t software;
    uint8_t vsync_requested;
    uint8_t vsync_reported;
} np_renderer_info;

typedef struct np_frame_timing {
    uint64_t upload_ns;
    uint64_t render_present_ns;
} np_frame_timing;

/* All instance functions, including destroy, must run on the thread that called create. */
NP_API np_presenter *np_create(int32_t texture_width, int32_t texture_height,
                               int32_t window_width, int32_t window_height,
                               uint32_t flags);
NP_API void np_destroy(np_presenter *presenter);

/*
 * Upload and present one RGB24 frame. The texture is persistent across calls.
 * buffer_length must cover the last complete row: (height - 1) * stride + width * 3.
 */
NP_API int np_present_rgb24(np_presenter *presenter, const void *pixels,
                            size_t buffer_length, int32_t width, int32_t height,
                            int32_t stride);

/* Returns 1 for an event, 0 when the queue is empty, and -1 on error. */
NP_API int np_poll_event(np_presenter *presenter, np_event *event_out);
NP_API int np_get_renderer_info(np_presenter *presenter, np_renderer_info *info_out);
NP_API int np_get_last_timing(np_presenter *presenter, np_frame_timing *timing_out);

/* Last error for this calling thread. The pointer remains owned by the library. */
NP_API const char *np_last_error(void);

/* Pure helper used by event mapping and tests. Returns 1 inside the image, 0 in letterboxing. */
NP_API int np_map_window_to_logical(int32_t logical_width, int32_t logical_height,
                                    int32_t window_width, int32_t window_height,
                                    float window_x, float window_y,
                                    float *logical_x, float *logical_y);

#ifdef __cplusplus
}
#endif

#endif
