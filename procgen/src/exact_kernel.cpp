#include "exact_kernel.hpp"

#include <Delaunay_psm.h>

#include <mutex>

namespace pg::detail {

void ensure_exact_kernel() {
    static std::once_flag once;
    std::call_once(once, [] {
        GEO::initialize(GEO::GEOGRAM_INSTALL_NONE);
        if (GEO::Logger::instance() != nullptr) {
            GEO::Logger::instance()->set_quiet(true);
        }
        GEO::PCK::initialize();
        GEO::PCK::set_SOS_mode(GEO::PCK::SOS_LEXICO);
        GEO::Delaunay::initialize();
    });
}

}  // namespace pg::detail
