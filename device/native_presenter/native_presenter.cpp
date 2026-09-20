#include "native_presenter.h"

#include <SDL.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <limits>
#include <mutex>
#include <new>
#include <string>
#include <thread>

namespace {

thread_local std::string last_error;
std::mutex init_mutex;
unsigned presenter_count = 0;
bool library_owns_video = false;

void set_error(const char *message) {
    last_error = message ? message : "unknown error";
}

void set_sdl_error(const char *operation) {
    last_error = std::string(operation) + ": " + SDL_GetError();
}

template <size_t N>
void copy_text(char (&destination)[N], const char *source) {
    std::memset(destination, 0, N);
    if (source) {
        std::strncpy(destination, source, N - 1);
    }
}

bool acquire_video() {
    std::lock_guard<std::mutex> lock(init_mutex);
    if (presenter_count == 0 && (SDL_WasInit(SDL_INIT_VIDEO) & SDL_INIT_VIDEO) == 0) {
        if (SDL_InitSubSystem(SDL_INIT_VIDEO) != 0) {
            set_sdl_error("SDL_InitSubSystem(SDL_INIT_VIDEO)");
            return false;
        }
        library_owns_video = true;
    }
    ++presenter_count;
    return true;
}

void release_video() {
    std::lock_guard<std::mutex> lock(init_mutex);
    if (presenter_count > 0) {
        --presenter_count;
    }
    if (presenter_count == 0 && library_owns_video) {
        SDL_QuitSubSystem(SDL_INIT_VIDEO);
        library_owns_video = false;
    }
}

using Clock = std::chrono::steady_clock;
uint64_t elapsed_ns(Clock::time_point start, Clock::time_point finish) {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(finish - start).count());
}

}  // namespace

struct np_presenter {
    SDL_Window *window = nullptr;
    SDL_Renderer *renderer = nullptr;
    SDL_Texture *texture = nullptr;
    int texture_width = 0;
    int texture_height = 0;
    bool vsync_requested = false;
    bool acquired_video = false;
    std::thread::id owner;
    np_frame_timing timing{};
};

namespace {

bool on_owner_thread(np_presenter *presenter) {
    if (!presenter) {
        set_error("presenter is null");
        return false;
    }
    if (presenter->owner != std::this_thread::get_id()) {
        set_error("presenter function called from a thread other than its creator");
        return false;
    }
    return true;
}

void cleanup(np_presenter *presenter) {
    if (!presenter) {
        return;
    }
    if (presenter->texture) {
        SDL_DestroyTexture(presenter->texture);
    }
    if (presenter->renderer) {
        SDL_DestroyRenderer(presenter->renderer);
    }
    if (presenter->window) {
        SDL_DestroyWindow(presenter->window);
    }
    if (presenter->acquired_video) {
        release_video();
    }
    delete presenter;
}

}  // namespace

extern "C" {

np_presenter *np_create(int32_t texture_width, int32_t texture_height,
                        int32_t window_width, int32_t window_height,
                        uint32_t flags) {
    last_error.clear();
    if (texture_width <= 0 || texture_height <= 0 || window_width <= 0 || window_height <= 0) {
        set_error("texture and window dimensions must be positive");
        return nullptr;
    }
    if (texture_width > std::numeric_limits<int>::max() / 3) {
        set_error("texture width is too large for RGB24");
        return nullptr;
    }
    if (!acquire_video()) {
        return nullptr;
    }

    auto *presenter = new (std::nothrow) np_presenter();
    if (!presenter) {
        release_video();
        set_error("could not allocate presenter");
        return nullptr;
    }
    presenter->acquired_video = true;
    presenter->owner = std::this_thread::get_id();
    presenter->texture_width = texture_width;
    presenter->texture_height = texture_height;
    presenter->vsync_requested = (flags & NP_VSYNC) != 0;

    uint32_t window_flags = (flags & NP_HIDDEN) ? SDL_WINDOW_HIDDEN : SDL_WINDOW_SHOWN;
    window_flags |= SDL_WINDOW_ALLOW_HIGHDPI;
    if (flags & NP_FULLSCREEN_DESKTOP) {
        window_flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
    }
    presenter->window = SDL_CreateWindow("Nimbus", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                         window_width, window_height, window_flags);
    if (!presenter->window) {
        set_sdl_error("SDL_CreateWindow");
        cleanup(presenter);
        return nullptr;
    }

    uint32_t renderer_flags = SDL_RENDERER_ACCELERATED;
    if (flags & NP_VSYNC) {
        renderer_flags |= SDL_RENDERER_PRESENTVSYNC;
    }
    presenter->renderer = SDL_CreateRenderer(presenter->window, -1, renderer_flags);
    if (!presenter->renderer && !(flags & NP_REQUIRE_ACCELERATED)) {
        /* The caller can see this fallback through renderer_info.accelerated/software. */
        presenter->renderer = SDL_CreateRenderer(
            presenter->window, -1, (flags & NP_VSYNC) ? SDL_RENDERER_PRESENTVSYNC : 0);
    }
    if (!presenter->renderer) {
        set_sdl_error("SDL_CreateRenderer");
        cleanup(presenter);
        return nullptr;
    }

    presenter->texture = SDL_CreateTexture(presenter->renderer, SDL_PIXELFORMAT_RGB24,
                                           SDL_TEXTUREACCESS_STREAMING,
                                           texture_width, texture_height);
    if (!presenter->texture) {
        set_sdl_error("SDL_CreateTexture(RGB24 streaming)");
        cleanup(presenter);
        return nullptr;
    }
    SDL_SetRenderDrawColor(presenter->renderer, 0, 0, 0, 255);
    return presenter;
}

void np_destroy(np_presenter *presenter) {
    last_error.clear();
    if (!presenter) {
        return;
    }
    if (!on_owner_thread(presenter)) {
        return;
    }
    cleanup(presenter);
}

int np_present_rgb24(np_presenter *presenter, const void *pixels,
                     size_t buffer_length, int32_t width, int32_t height,
                     int32_t stride) {
    last_error.clear();
    if (!on_owner_thread(presenter)) {
        return -1;
    }
    if (!pixels) {
        set_error("pixels is null");
        return -1;
    }
    if (width != presenter->texture_width || height != presenter->texture_height) {
        set_error("frame dimensions do not match the persistent texture");
        return -1;
    }
    if (width <= 0 || height <= 0 || width > std::numeric_limits<int>::max() / 3) {
        set_error("invalid RGB24 frame dimensions");
        return -1;
    }
    const size_t row_bytes = static_cast<size_t>(width) * 3;
    if (stride < 0 || static_cast<size_t>(stride) < row_bytes) {
        set_error("stride is smaller than one RGB24 row");
        return -1;
    }
    const size_t rows_before_last = static_cast<size_t>(height - 1);
    if (rows_before_last > (std::numeric_limits<size_t>::max() - row_bytes) /
                               static_cast<size_t>(stride)) {
        set_error("frame buffer length calculation overflowed");
        return -1;
    }
    const size_t required = rows_before_last * static_cast<size_t>(stride) + row_bytes;
    if (buffer_length < required) {
        set_error("buffer is shorter than the declared dimensions and stride");
        return -1;
    }

    void *texture_pixels = nullptr;
    int texture_pitch = 0;
    auto upload_start = Clock::now();
    if (SDL_LockTexture(presenter->texture, nullptr, &texture_pixels, &texture_pitch) != 0) {
        set_sdl_error("SDL_LockTexture");
        return -1;
    }
    if (texture_pitch < 0 || static_cast<size_t>(texture_pitch) < row_bytes) {
        SDL_UnlockTexture(presenter->texture);
        set_error("SDL returned a texture pitch smaller than one RGB24 row");
        return -1;
    }
    const auto *source = static_cast<const uint8_t *>(pixels);
    auto *destination = static_cast<uint8_t *>(texture_pixels);
    for (int y = 0; y < height; ++y) {
        std::memcpy(destination + static_cast<size_t>(y) * texture_pitch,
                    source + static_cast<size_t>(y) * stride, row_bytes);
    }
    SDL_UnlockTexture(presenter->texture);
    auto upload_finish = Clock::now();

    int output_width = 0;
    int output_height = 0;
    if (SDL_GetRendererOutputSize(presenter->renderer, &output_width, &output_height) != 0) {
        set_sdl_error("SDL_GetRendererOutputSize");
        return -1;
    }
    const double scale = std::min(static_cast<double>(output_width) / width,
                                  static_cast<double>(output_height) / height);
    SDL_Rect destination_rect{
        static_cast<int>(std::lround((output_width - width * scale) / 2.0)),
        static_cast<int>(std::lround((output_height - height * scale) / 2.0)),
        static_cast<int>(std::lround(width * scale)),
        static_cast<int>(std::lround(height * scale)),
    };
    auto render_start = Clock::now();
    if (SDL_RenderClear(presenter->renderer) != 0 ||
        SDL_RenderCopy(presenter->renderer, presenter->texture, nullptr, &destination_rect) != 0) {
        set_sdl_error("SDL_RenderClear/SDL_RenderCopy");
        return -1;
    }
    SDL_RenderPresent(presenter->renderer);
    auto render_finish = Clock::now();
    presenter->timing.upload_ns = elapsed_ns(upload_start, upload_finish);
    presenter->timing.render_present_ns = elapsed_ns(render_start, render_finish);
    return 0;
}

int np_map_window_to_logical(int32_t logical_width, int32_t logical_height,
                             int32_t window_width, int32_t window_height,
                             float window_x, float window_y,
                             float *logical_x, float *logical_y) {
    last_error.clear();
    if (logical_width <= 0 || logical_height <= 0 || window_width <= 0 || window_height <= 0 ||
        !logical_x || !logical_y || !std::isfinite(window_x) || !std::isfinite(window_y)) {
        set_error("invalid coordinate mapping arguments");
        return -1;
    }
    const double scale = std::min(static_cast<double>(window_width) / logical_width,
                                  static_cast<double>(window_height) / logical_height);
    const double drawn_width = logical_width * scale;
    const double drawn_height = logical_height * scale;
    const double offset_x = (window_width - drawn_width) / 2.0;
    const double offset_y = (window_height - drawn_height) / 2.0;
    *logical_x = static_cast<float>((window_x - offset_x) / scale);
    *logical_y = static_cast<float>((window_y - offset_y) / scale);
    return *logical_x >= 0.0f && *logical_x < logical_width &&
                   *logical_y >= 0.0f && *logical_y < logical_height
               ? 1
               : 0;
}

int np_poll_event(np_presenter *presenter, np_event *event_out) {
    last_error.clear();
    if (!on_owner_thread(presenter)) {
        return -1;
    }
    if (!event_out) {
        set_error("event_out is null");
        return -1;
    }
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        std::memset(event_out, 0, sizeof(*event_out));
        if (event.type == SDL_QUIT ||
            (event.type == SDL_WINDOWEVENT && event.window.event == SDL_WINDOWEVENT_CLOSE)) {
            event_out->type = NP_EVENT_QUIT;
            return 1;
        }
        if (event.type == SDL_MOUSEBUTTONDOWN || event.type == SDL_MOUSEBUTTONUP) {
            if (event.button.which == SDL_TOUCH_MOUSEID) {
                continue;
            }
            int ww = 0;
            int wh = 0;
            SDL_GetWindowSize(presenter->window, &ww, &wh);
            float x = 0;
            float y = 0;
            const int inside = np_map_window_to_logical(
                presenter->texture_width, presenter->texture_height, ww, wh,
                static_cast<float>(event.button.x), static_cast<float>(event.button.y), &x, &y);
            if (inside < 0) {
                return -1;
            }
            event_out->type = event.type == SDL_MOUSEBUTTONDOWN
                                  ? NP_EVENT_POINTER_DOWN
                                  : NP_EVENT_POINTER_UP;
            event_out->x = x;
            event_out->y = y;
            event_out->pressure = event.type == SDL_MOUSEBUTTONDOWN ? 1.0f : 0.0f;
            event_out->inside = static_cast<uint8_t>(inside);
            return 1;
        }
        if (event.type == SDL_FINGERDOWN || event.type == SDL_FINGERUP) {
            int ww = 0;
            int wh = 0;
            SDL_GetWindowSize(presenter->window, &ww, &wh);
            float x = 0;
            float y = 0;
            const int inside = np_map_window_to_logical(
                presenter->texture_width, presenter->texture_height, ww, wh,
                event.tfinger.x * ww, event.tfinger.y * wh, &x, &y);
            if (inside < 0) {
                return -1;
            }
            event_out->type = event.type == SDL_FINGERDOWN
                                  ? NP_EVENT_POINTER_DOWN
                                  : NP_EVENT_POINTER_UP;
            event_out->x = x;
            event_out->y = y;
            event_out->pressure = event.tfinger.pressure;
            event_out->inside = static_cast<uint8_t>(inside);
            return 1;
        }
        if (event.type == SDL_KEYDOWN || event.type == SDL_KEYUP) {
            event_out->type = event.type == SDL_KEYDOWN ? NP_EVENT_KEY_DOWN : NP_EVENT_KEY_UP;
            event_out->keycode = event.key.keysym.sym;
            event_out->repeat = event.key.repeat;
            return 1;
        }
    }
    return 0;
}

int np_get_renderer_info(np_presenter *presenter, np_renderer_info *info_out) {
    last_error.clear();
    if (!on_owner_thread(presenter)) {
        return -1;
    }
    if (!info_out) {
        set_error("info_out is null");
        return -1;
    }
    SDL_RendererInfo sdl_info{};
    if (SDL_GetRendererInfo(presenter->renderer, &sdl_info) != 0) {
        set_sdl_error("SDL_GetRendererInfo");
        return -1;
    }
    std::memset(info_out, 0, sizeof(*info_out));
    copy_text(info_out->renderer_name, sdl_info.name);
    copy_text(info_out->video_driver, SDL_GetCurrentVideoDriver());
    info_out->renderer_flags = sdl_info.flags;
    info_out->texture_width = presenter->texture_width;
    info_out->texture_height = presenter->texture_height;
    if (SDL_GetRendererOutputSize(presenter->renderer, &info_out->output_width,
                                  &info_out->output_height) != 0) {
        set_sdl_error("SDL_GetRendererOutputSize");
        return -1;
    }
    info_out->accelerated = (sdl_info.flags & SDL_RENDERER_ACCELERATED) != 0;
    info_out->software = (sdl_info.flags & SDL_RENDERER_SOFTWARE) != 0;
    info_out->vsync_requested = presenter->vsync_requested;
    info_out->vsync_reported = (sdl_info.flags & SDL_RENDERER_PRESENTVSYNC) != 0;
    return 0;
}

int np_get_last_timing(np_presenter *presenter, np_frame_timing *timing_out) {
    last_error.clear();
    if (!on_owner_thread(presenter)) {
        return -1;
    }
    if (!timing_out) {
        set_error("timing_out is null");
        return -1;
    }
    *timing_out = presenter->timing;
    return 0;
}

const char *np_last_error(void) {
    return last_error.c_str();
}

}  // extern "C"
