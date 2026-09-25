#include <inc/Core/Common/WorkSpace.h>
#include <iostream>

using Table=SPTAG::COMMON::OptHashPosVector;
void check(bool value) {
    if (!value) throw std::runtime_error("Storage boundary failure");
}
template<class F> void rejects(F f) {
    bool caught=false;
    try { f(); } catch (const std::exception&) { caught=true; }
    check(caught);
}
int main() {
    Table table;table.Init(1,0);
    int evaluations=0;
    auto predicate=[&](int id){++evaluations;return id%2==0;};
    table.EnableNativeMatch(SPTAG::MaxSize);
    for(int id=0;id<8192;++id) check(!table.CheckAndSetMatch(id,predicate).first);
    for(int id:{SPTAG::MaxSize-2,SPTAG::MaxSize-1}) {
        check(!table.CheckAndSetMatch(id,predicate).first);
        check(table.CheckAndSetMatch(id,predicate).second==(id%2==0));
    }
    check(table.SlotBytes()==4 && evaluations==8194);
    table.DoubleSize();
    table.EnableNativeMatch(SPTAG::MaxSize);
    check(table.CheckAndSetMatch(SPTAG::MaxSize-1,predicate).second && evaluations==8194);
    rejects([&]{table.CheckAndSetMatch(SPTAG::MaxSize,predicate);});
    rejects([&]{table.CheckAndSetMatch(-1,predicate);});
    rejects([&]{table.EnableNativeMatch(16);});
    table.EnableMatch(true);
    check(!table.Contains(SPTAG::MaxSize-1));
    check(!table.CheckAndSetMatch(SPTAG::MaxSize,predicate).first);
    check(table.Contains(SPTAG::MaxSize) && table.SlotBytes()==8);
    table.DoubleSize();check(table.Contains(SPTAG::MaxSize));
    table.ResetQuery();table.EnableNativeMatch(16);
    check(!table.CheckAndSetMatch(15,predicate).first);
    rejects([&]{table.CheckAndSetMatch(16,predicate);});
    table.ResetQuery();
    check(!table.CheckAndSet(SPTAG::MaxSize) && table.CheckAndSet(SPTAG::MaxSize));
    rejects([&]{table.Init(SPTAG::MaxSize,0);});
    rejects([&]{table.Init(1,31);});
    rejects([&]{table.Init(-1,0);});
    std::cout<<"PASS UBSan packed native highest/sentinel, generic domain restoration/full INT_MAX, both-block growth and reset\n";
}
