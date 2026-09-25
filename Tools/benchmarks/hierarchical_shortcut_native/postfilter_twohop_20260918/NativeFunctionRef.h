#pragma once
#include <memory>
#include <utility>
#include <type_traits>

namespace SPTAG { namespace COMMON {
template<class> class NativeFunctionRef;
template<class R, class... Args>
class NativeFunctionRef<R(Args...)> {
    const void* object = nullptr;
    R (*invoke)(const void*, Args...) = nullptr;
public:
    NativeFunctionRef() = default;
    // Lvalues only: the synchronous caller owns the callable's lifetime.
    template<class F, std::enable_if_t<!std::is_same<std::decay_t<F>, NativeFunctionRef>::value, int> = 0>
    NativeFunctionRef(F& f)
        : object(std::addressof(f)), invoke([](const void* p, Args... args) -> R {
            return (*static_cast<const F*>(p))(std::forward<Args>(args)...);
        }) {}
    explicit operator bool() const { return invoke != nullptr; }
    R operator()(Args... args) const { return invoke(object, std::forward<Args>(args)...); }
};
}}
