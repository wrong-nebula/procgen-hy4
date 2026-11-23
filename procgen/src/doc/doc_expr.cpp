#include "doc_expr.hpp"

#include <algorithm>
#include <cmath>

namespace pg::doc::detail {
namespace {

inline bool in_finite_range(float v) noexcept {
    return std::isfinite(v) && std::abs(v) <= 1'000'000.0f;
}

inline void check_range(float v) {
    if (!in_finite_range(v)) fail("Numeric expression exceeds its finite range");
}

inline float eval_unary(ExprOp op, float a) noexcept {
    switch (op) {
        case ExprOp::Sin: return std::sin(a);
        case ExprOp::Cos: return std::cos(a);
        case ExprOp::Tan: return std::tan(a);
        case ExprOp::Sqrt: return std::sqrt(a);
        case ExprOp::Abs: return std::abs(a);
        case ExprOp::Neg: return -a;
        case ExprOp::Floor: return std::floor(a);
        case ExprOp::Ceil: return std::ceil(a);
        case ExprOp::Round: return std::round(a);
        default: return a;
    }
}

inline float eval_binary(ExprOp op, float a, float b) noexcept {
    switch (op) {
        case ExprOp::Add: return a + b;
        case ExprOp::Sub: return a - b;
        case ExprOp::Mul: return a * b;
        case ExprOp::Div: return a / b;
        case ExprOp::Min: return std::min(a, b);
        case ExprOp::Max: return std::max(a, b);
        case ExprOp::Gt: return a > b ? 1.0f : 0.0f;
        case ExprOp::Lt: return a < b ? 1.0f : 0.0f;
        case ExprOp::Pow: return std::pow(a, b);
        case ExprOp::Atan2: return std::atan2(a, b);
        case ExprOp::Mod: return std::fmod(a, b);
        default: return 0.0f;
    }
}

}  // namespace

CompiledExpr::CompiledExpr(const json::Value* expr, const Controls* controls, float fallback) {
    if (!expr) {
        is_const_ = true;
        const_val_ = fallback;
        check_range(const_val_);
        tape_.push_back({ExprOp::PushConst, 0, 0, const_val_});
        return;
    }
    float folded = 0.0f;
    if (compile_node(*expr, controls, folded)) {
        tape_.clear();
        tape_.push_back({ExprOp::PushConst, 0, 0, folded});
        is_const_ = true;
        const_val_ = folded;
    } else {
        is_const_ = false;
        const_val_ = 0.0f;
    }
}

std::uint16_t CompiledExpr::add_error(std::string msg) {
    const auto idx = static_cast<std::uint16_t>(errors_.size());
    errors_.push_back(std::move(msg));
    return idx;
}

bool CompiledExpr::compile_node(const json::Value& v, const Controls* controls, float& folded_out) {
    if (v.is_number()) {
        const auto val = static_cast<float>(v.number);
        if (in_finite_range(val)) {
            tape_.push_back({ExprOp::PushConst, 0, 0, val});
            folded_out = val;
            return true;
        }
        tape_.push_back({ExprOp::Fail, 0, add_error("Numeric expression exceeds its finite range"), 0.0f});
        return false;
    }
    if (v.is_string()) {
        const std::string& key = v.string;
        if (key.starts_with("@")) {
            if (key == "@index") tape_.push_back({ExprOp::PushIndex});
            else if (key == "@x") tape_.push_back({ExprOp::PushX});
            else if (key == "@y") tape_.push_back({ExprOp::PushY});
            else if (key == "@z") tape_.push_back({ExprOp::PushZ});
            else if (key == "@t" || key == "@u") tape_.push_back({ExprOp::PushU, 0, add_error("Unknown or unavailable point field " + key)});
            else if (key == "@v") tape_.push_back({ExprOp::PushV, 0, add_error("Unknown or unavailable point field " + key)});
            else if (key == "@random") tape_.push_back({ExprOp::PushRandom});
            else tape_.push_back({ExprOp::Fail, 0, add_error("Unknown or unavailable point field " + key)});
            return false;
        }
        if (!key.starts_with("$") || !controls) {
            tape_.push_back({ExprOp::Fail, 0, add_error("Unknown numeric control " + key)});
            return false;
        }
        const auto it = controls->find(key.substr(1));
        if (it == controls->end()) {
            tape_.push_back({ExprOp::Fail, 0, add_error("Unknown numeric control " + key)});
            return false;
        }
        const float val = it->second;
        if (!in_finite_range(val)) {
            tape_.push_back({ExprOp::Fail, 0, add_error("Numeric expression exceeds its finite range")});
            return false;
        }
        tape_.push_back({ExprOp::PushConst, 0, 0, val});
        folded_out = val;
        return true;
    }
    if (v.is_array()) {
        if (v.array.size() < 2 || v.array.size() > 4 || !v.array[0].is_string()) {
            tape_.push_back({ExprOp::Fail, 0, add_error("Expected [operator, left, right]")});
            return false;
        }
        const std::string& op = v.array[0].string;
        if (v.array.size() == 3) {
            ExprOp bop = ExprOp::Fail;
            if (op == "+") bop = ExprOp::Add;
            else if (op == "-") bop = ExprOp::Sub;
            else if (op == "*") bop = ExprOp::Mul;
            else if (op == "/") bop = ExprOp::Div;
            else if (op == "min") bop = ExprOp::Min;
            else if (op == "max") bop = ExprOp::Max;
            else if (op == ">") bop = ExprOp::Gt;
            else if (op == "<") bop = ExprOp::Lt;
            else if (op == "pow") bop = ExprOp::Pow;
            else if (op == "atan2") bop = ExprOp::Atan2;
            else if (op == "mod") bop = ExprOp::Mod;

            const std::size_t mark = tape_.size();
            float a = 0.0f, b = 0.0f;
            const bool ca = compile_node(v.array[1], controls, a);
            const bool cb = compile_node(v.array[2], controls, b);
            if (bop == ExprOp::Fail) {
                tape_.push_back({ExprOp::Fail, 0, add_error("Unknown numeric operator " + op)});
                return false;
            }
            if (ca && cb) {
                const float res = eval_binary(bop, a, b);
                if (in_finite_range(res)) {
                    tape_.resize(mark);
                    tape_.push_back({ExprOp::PushConst, 0, 0, res});
                    folded_out = res;
                    return true;
                }
            }
            tape_.push_back({bop});
            return false;
        }
        if (v.array.size() == 2) {
            ExprOp uop = ExprOp::Fail;
            if (op == "sin") uop = ExprOp::Sin;
            else if (op == "cos") uop = ExprOp::Cos;
            else if (op == "tan") uop = ExprOp::Tan;
            else if (op == "sqrt") uop = ExprOp::Sqrt;
            else if (op == "abs") uop = ExprOp::Abs;
            else if (op == "neg") uop = ExprOp::Neg;
            else if (op == "floor") uop = ExprOp::Floor;
            else if (op == "ceil") uop = ExprOp::Ceil;
            else if (op == "round") uop = ExprOp::Round;

            const std::size_t mark = tape_.size();
            float a = 0.0f;
            const bool ca = compile_node(v.array[1], controls, a);
            if (uop == ExprOp::Fail) {
                tape_.push_back({ExprOp::Fail, 0, add_error("Unknown numeric function " + op)});
                return false;
            }
            if (ca) {
                const float res = eval_unary(uop, a);
                if (in_finite_range(res)) {
                    tape_.resize(mark);
                    tape_.push_back({ExprOp::PushConst, 0, 0, res});
                    folded_out = res;
                    return true;
                }
            }
            tape_.push_back({uop});
            return false;
        }
        // v.array.size() == 4: ["if", cond, then, else]
        if (op != "if") {
            tape_.push_back({ExprOp::Fail, 0, add_error("Expected [operator, left, right]")});
            return false;
        }
        const std::size_t mark = tape_.size();
        float cond_val = 0.0f;
        if (compile_node(v.array[1], controls, cond_val)) {
            tape_.resize(mark);
            return compile_node(v.array[cond_val != 0.0f ? 2 : 3], controls, folded_out);
        }
        const std::size_t jz_idx = tape_.size();
        tape_.push_back({ExprOp::JumpIfZero, 0, 0, 0.0f});
        float dummy = 0.0f;
        (void)compile_node(v.array[2], controls, dummy);
        const std::size_t jmp_idx = tape_.size();
        tape_.push_back({ExprOp::Jump, 0, 0, 0.0f});
        tape_[jz_idx].arg = static_cast<std::uint16_t>(tape_.size());
        (void)compile_node(v.array[3], controls, dummy);
        tape_[jmp_idx].arg = static_cast<std::uint16_t>(tape_.size());
        return false;
    }
    tape_.push_back({ExprOp::Fail, 0, add_error("Expected a number, control or expression")});
    return false;
}

float CompiledExpr::eval(const Point* point) const {
    if (is_const_) return const_val_;
    float stack[96];
    int sp = 0;
    auto push = [&](float v) {
        check_range(v);
        if (sp >= 96) fail("Expression stack depth exceeded");
        stack[sp++] = v;
    };
    const std::size_t n = tape_.size();
    std::size_t pc = 0;
    while (pc < n) {
        const ExprInsn ins = tape_[pc++];
        switch (ins.op) {
            case ExprOp::PushConst:
                stack[sp++] = ins.imm;  // already range-checked at compile time
                break;
            case ExprOp::PushIndex:
                if (!point) fail("Unknown or unavailable point field @index");
                push(static_cast<float>(point->index));
                break;
            case ExprOp::PushX:
                if (!point) fail("Unknown or unavailable point field @x");
                push(static_cast<float>(point->position.x));
                break;
            case ExprOp::PushY:
                if (!point) fail("Unknown or unavailable point field @y");
                push(static_cast<float>(point->position.y));
                break;
            case ExprOp::PushZ:
                if (!point) fail("Unknown or unavailable point field @z");
                push(static_cast<float>(point->position.z));
                break;
            case ExprOp::PushU:
                if (!point || !point->parametric) fail(errors_[ins.arg]);
                push(point->u);
                break;
            case ExprOp::PushV:
                if (!point || !point->parametric) fail(errors_[ins.arg]);
                push(point->v);
                break;
            case ExprOp::PushRandom: {
                if (!point) fail("Unknown or unavailable point field @random");
                std::uint32_t bits = static_cast<std::uint32_t>(point->index) + static_cast<std::uint32_t>(point->seed) * 0x9E3779B9u;
                bits = (bits ^ (bits >> 16)) * 0x7FEB352Du;
                bits = (bits ^ (bits >> 15)) * 0x846CA68Bu;
                bits ^= bits >> 16;
                push(static_cast<float>(bits >> 8) / 16777216.0f);
                break;
            }
            case ExprOp::Sin:
            case ExprOp::Cos:
            case ExprOp::Tan:
            case ExprOp::Sqrt:
            case ExprOp::Abs:
            case ExprOp::Neg:
            case ExprOp::Floor:
            case ExprOp::Ceil:
            case ExprOp::Round: {
                const float r = eval_unary(ins.op, stack[sp - 1]);
                check_range(r);
                stack[sp - 1] = r;
                break;
            }
            case ExprOp::Add:
            case ExprOp::Sub:
            case ExprOp::Mul:
            case ExprOp::Div:
            case ExprOp::Min:
            case ExprOp::Max:
            case ExprOp::Gt:
            case ExprOp::Lt:
            case ExprOp::Pow:
            case ExprOp::Atan2:
            case ExprOp::Mod: {
                const float b = stack[--sp];
                const float r = eval_binary(ins.op, stack[sp - 1], b);
                check_range(r);
                stack[sp - 1] = r;
                break;
            }
            case ExprOp::JumpIfZero: {
                const float cond = stack[--sp];
                if (cond == 0.0f) pc = ins.arg;
                break;
            }
            case ExprOp::Jump:
                pc = ins.arg;
                break;
            case ExprOp::Fail:
                fail(errors_[ins.arg]);
        }
    }
    return stack[0];
}

CompiledVec3::CompiledVec3(const json::Value* vec, const Controls* controls, Vec3 fallback) {
    if (!vec) {
        c_[0] = CompiledExpr(nullptr, controls, static_cast<float>(fallback.x));
        c_[1] = CompiledExpr(nullptr, controls, static_cast<float>(fallback.y));
        c_[2] = CompiledExpr(nullptr, controls, static_cast<float>(fallback.z));
        return;
    }
    if (!vec->is_array() || vec->array.size() != 3) fail("Expected a three-component vector");
    c_[0] = CompiledExpr(&vec->array[0], controls, 0.0f);
    c_[1] = CompiledExpr(&vec->array[1], controls, 0.0f);
    c_[2] = CompiledExpr(&vec->array[2], controls, 0.0f);
}

CompiledTransform::CompiledTransform(const Options& o)
    : translation_(o.get("translation"), o.controls, {0, 0, 0}),
      rotation_(o.get("rotation"), o.controls, {0, 0, 0}),
      scale_(o.get("scale"), o.controls, {1, 1, 1}) {
    if (rotation_.is_constant() && scale_.is_constant()) {
        const Vec3 r = rotation_.eval(nullptr), s = scale_.eval(nullptr);
        const double smin = std::min({std::abs(s.x), std::abs(s.y), std::abs(s.z)});
        const double smax = std::max({std::abs(s.x), std::abs(s.y), std::abs(s.z)});
        const_rs_valid_ = (smin >= 0.0001 && smax <= 1000.0);
        if (const_rs_valid_) base_rs_ = compose({0, 0, 0}, r, s);
        const_rs_ = true;
    }
}

Mat4 CompiledTransform::eval(const Point* point) const {
    const Vec3 p = translation_.eval(point);
    if (const_rs_) {
        if (!const_rs_valid_) fail("Scale magnitude must be between 0.0001 and 1000");
        Mat4 m = base_rs_;
        m.c[3][0] = p.x;
        m.c[3][1] = p.y;
        m.c[3][2] = p.z;
        return m;
    }
    const Vec3 r = rotation_.eval(point), s = scale_.eval(point);
    const double smin = std::min({std::abs(s.x), std::abs(s.y), std::abs(s.z)});
    const double smax = std::max({std::abs(s.x), std::abs(s.y), std::abs(s.z)});
    if (!(smin >= 0.0001 && smax <= 1000.0)) fail("Scale magnitude must be between 0.0001 and 1000");
    return compose(p, r, s);
}

}  // namespace pg::doc::detail
