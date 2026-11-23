#pragma once

namespace pg::detail {

// Thread-safe one-time initialization of Geogram's exact predicate kernel
// (GEO::initialize + silent logger + PCK::initialize + SOS_LEXICO).
void ensure_exact_kernel();

}  // namespace pg::detail
