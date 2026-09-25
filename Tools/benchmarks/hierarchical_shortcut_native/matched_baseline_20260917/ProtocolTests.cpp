#include "NativeNProbeSweep.h"
#include "inc/Core/Common/WorkSpace.h"
#include <iostream>

int main()
{
    const std::vector<int> grid{16,24,32,48,62,80,96,128,192,256,384};
    if (NativeNProbeSweep::Parse("[16,24,32,48,62,80,96,128,192,256,384]", 10) != grid)
        throw std::runtime_error("Array shape changed");
    const auto reversed = NativeNProbeSweep::Parse("[384,256,192,128,96,80,62,48,32,24,16]", 10);
    if (!std::equal(grid.begin(), grid.end(), reversed.rbegin()))
        throw std::runtime_error("Reverse array changed");
    for (const char* invalid : {"", "[24,24]", "[9]", "[24,]", "[24,x]", "[-24]"}) {
        bool rejected = false;
        try { NativeNProbeSweep::Parse(invalid, 10); }
        catch (const std::invalid_argument&) { rejected = true; }
        if (!rejected) throw std::runtime_error("Invalid native array accepted");
    }
    SPTAG::COMMON::ThreadLocalWorkSpaceFactory<SPTAG::COMMON::WorkSpace> factory;
    factory.ReturnWorkSpace(std::make_unique<SPTAG::COMMON::WorkSpace>());
    factory.m_workspace.reset();
    if (factory.GetWorkSpace()) throw std::runtime_error("Native workspace was retained");
    std::cout << "Strict native arrays/reverse order/workspace release passed\n";
}
