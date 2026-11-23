#pragma once
// Flat stack-machine bytecode compiler for kitlib numeric expressions.
// Eliminates JSON AST walking, string comparisons and std::map control lookups
// inside inner geometry loops (@t curves, @u/@v patches, sweeps, multi-instances),
// while preserving exact 32-bit float semantics, short-circuit `if` evaluation
// and error messages of Options::number.

#include "doc_internal.hpp"

#include <cstdint>
#include <string>
#include <vector>

namespace pg::doc::detail {

enum class ExprOp : std::uint8_t {
    PushConst,
    PushIndex,
    PushX,
    PushY,
    PushZ,
    PushU,
    PushV,
    PushRandom,
    Sin,
    Cos,
    Tan,
    Sqrt,
    Abs,
    Neg,
    Floor,
    Ceil,
    Round,
    Add,
    Sub,
    Mul,
    Div,
    Min,
    Max,
    Gt,
    Lt,
    Pow,
    Atan2,
    Mod,
    JumpIfZero,
    Jump,
    Fail,
};

struct ExprInsn {
    ExprOp op = ExprOp::PushConst;
    std::uint8_t pad = 0;
    std::uint16_t arg = 0;
    float imm = 0.0f;
};
static_assert(sizeof(ExprInsn) == 8, "ExprInsn must stay 8 bytes for cache-line locality");

class CompiledExpr {
public:
    CompiledExpr() = default;
    CompiledExpr(const json::Value* expr, const Controls* controls, float fallback = 0.0f);

    [[nodiscard]] bool is_constant() const noexcept { return is_const_; }
    [[nodiscard]] float constant_value() const noexcept { return const_val_; }
    [[nodiscard]] std::size_t instruction_count() const noexcept { return tape_.size(); }
    [[nodiscard]] float eval(const Point* point) const;

private:
    bool is_const_ = true;
    float const_val_ = 0.0f;
    std::vector<ExprInsn> tape_;
    std::vector<std::string> errors_;

    std::uint16_t add_error(std::string msg);
    bool compile_node(const json::Value& v, const Controls* controls, float& folded_out);
};

class CompiledVec3 {
public:
    CompiledVec3() = default;
    CompiledVec3(const json::Value* vec, const Controls* controls, Vec3 fallback = {});

    [[nodiscard]] bool is_constant() const noexcept {
        return c_[0].is_constant() && c_[1].is_constant() && c_[2].is_constant();
    }
    [[nodiscard]] Vec3 eval(const Point* point) const {
        return {c_[0].eval(point), c_[1].eval(point), c_[2].eval(point)};
    }

private:
    CompiledExpr c_[3];
};

class CompiledTransform {
public:
    explicit CompiledTransform(const Options& o);
    [[nodiscard]] Mat4 eval(const Point* point) const;

private:
    CompiledVec3 translation_;
    CompiledVec3 rotation_;
    CompiledVec3 scale_;
    bool const_rs_ = false;
    bool const_rs_valid_ = true;
    Mat4 base_rs_ = Mat4::identity();
};

}  // namespace pg::doc::detail
