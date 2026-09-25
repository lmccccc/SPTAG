#include "NativeNeighborHooks.h"
// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_COMMON_WORKSPACE_H_
#define _SPTAG_COMMON_WORKSPACE_H_

#include "inc/Core/SearchResult.h"
#include "CommonUtils.h"
#include "Heap.h"

#include <stdarg.h>
#include <functional>
#include <limits>
#include <stdexcept>

namespace SPTAG
{
    namespace COMMON
    {
        template <typename WorkSpaceType>
        class IWorkSpaceFactory
        {
        public:
            using Ptr = std::unique_ptr<WorkSpaceType>;

            virtual std::unique_ptr<WorkSpaceType> GetWorkSpace() = 0;
            virtual void ReturnWorkSpace(std::unique_ptr<WorkSpaceType> ws) = 0;
        };

        template <typename WorkSpaceType>
        class ThreadLocalWorkSpaceFactory : public IWorkSpaceFactory<WorkSpaceType>
        {
        public:
            static thread_local std::unique_ptr<WorkSpaceType> m_workspace;

            virtual std::unique_ptr< WorkSpaceType> GetWorkSpace() override
            {
                return std::move(m_workspace);
            }

            virtual void ReturnWorkSpace(std::unique_ptr<WorkSpaceType> ws) override
            {
                m_workspace = std::move(ws);
            }
        };

	template <typename WorkSpaceType>
        class SharedPoolWorkSpaceFactory : public IWorkSpaceFactory<WorkSpaceType> {
        public:
            virtual std::unique_ptr<WorkSpaceType> GetWorkSpace() override
	    {
                std::unique_ptr<WorkSpaceType>  ws;
                std::lock_guard<std::mutex> lock(m_mutex);
                if (!m_pool.empty()) {
                    ws = std::move(m_pool.back());
                    m_pool.pop_back();
                }
		return ws;
	    }

            void ReturnWorkSpace(std::unique_ptr<WorkSpaceType> ws) override {
                if (ws) {
                    std::lock_guard<std::mutex> lock(m_mutex);
                    m_pool.emplace_back(std::move(ws));
                }
            }

        private:
            std::mutex m_mutex;
            std::vector<std::unique_ptr<WorkSpaceType>> m_pool;
        };

        class OptHashPosVector
        {
        protected:
            // Max loop number in one hash block.
            static const int m_maxLoop = 8;

            // Could we use the second hash block.
            bool m_secondHash;

            int m_exp;

            // Max pool size.
            int m_poolSize;

            // Record 2 hash tables.
            // [0~m_poolSize + 1) is the first block.
            // [m_poolSize + 1, 2*(m_poolSize + 1)) is the second block;
            std::unique_ptr<std::uint32_t[]> m_hashTable;
            std::unique_ptr<std::uint64_t[]> m_matchTable;
            static constexpr std::uint64_t MatchFlag = std::uint64_t(1) << 32;

            static std::uint32_t Key(SizeType id) {
                static_assert(sizeof(SizeType)==4 && std::is_signed<SizeType>::value, "32-bit native IDs");
                if (id<0) throw std::out_of_range("Negative navigation ID");
                return static_cast<std::uint32_t>(id)+1U;
            }

            template<class Slot> Slot* Find(Slot* table,int size,std::uint32_t key) const {
                unsigned index=hash_func(key,size);
                for(int loop=0;loop<m_maxLoop;++loop) {
                    if (!table[index] || std::uint32_t(table[index])==key) return table+index;
                    index=hash_func2(index,size,loop);
                }
                return nullptr;
            }

            template<class Slot> void Grow(std::unique_ptr<Slot[]>& table) {
                const int oldSize=m_poolSize;
                int newSize=oldSize;
                for (;;) {
                    if (newSize>(std::numeric_limits<int>::max()/4)-1)
                        throw std::overflow_error("Native visited capacity exhausted");
                    newSize=((newSize+1)*2)-1;
                    std::unique_ptr<Slot[]> next(new Slot[std::size_t(newSize+1)*2]());
                    bool second=false,complete=true;
                    // Both old blocks, exactly [0, 2*(oldSize+1)); never the new-size boundary.
                    for (std::size_t i=0;i<std::size_t(oldSize+1)*2;++i) if (table[i]) {
                        auto* slot=Find(next.get(),newSize,std::uint32_t(table[i]));
                        if (!slot) {
                            slot=Find(next.get()+newSize+1,newSize,std::uint32_t(table[i]));
                            second=true;
                        }
                        if (!slot) { complete=false;break; }
                        *slot=table[i];
                    }
                    if (!complete) continue;
                    m_exp+=static_cast<int>(std::log2(double(newSize+1)/(oldSize+1)));
                    m_poolSize=newSize;m_secondHash=second;table=std::move(next);
                    return;
                }
            }

            template<class Slot,class Predicate>
            std::pair<bool,bool> Probe(std::unique_ptr<Slot[]>& table,SizeType id,
                                       Predicate&& predicate,bool rejectNegative) {
                const auto key=Key(id);
                for (;;) {
                    auto* slot=Find(table.get(),m_poolSize,key);
                    bool second=false;
                    if (!slot) { slot=Find(table.get()+m_poolSize+1,m_poolSize,key);second=true; }
                    if (!slot) { Grow(table);continue; }
                    if (*slot) return {true,(std::uint64_t(*slot)&MatchFlag)!=0};
                    const bool match=predicate(id);
                    if (!match && rejectNegative) return {false,false};
                    *slot=static_cast<Slot>(std::uint64_t(key)|(match?MatchFlag:0));
                    m_secondHash|=second;
                    return {false,match};
                }
            }


            inline unsigned hash_func2(unsigned idx, int poolSize, int loop) const
            {
                return (idx + loop) & poolSize;
            }


            inline unsigned hash_func(unsigned idx, int poolSize) const
            {
                return ((unsigned)(idx * 99991) + _rotl(idx, 2) + 101) & poolSize;
            }

        public:
            OptHashPosVector(): m_secondHash(false), m_exp(2), m_poolSize(8191) {}

            ~OptHashPosVector() { m_hashTable.reset(); }


            void Init(SizeType size, int exp)
            {
                int ex = 0;
                while (size != 0) {
                    ex++;
                    size >>= 1;
                }
                m_secondHash = true;
                m_exp = exp;
                m_poolSize = (1 << (ex + exp)) - 1;
                m_matchTable.reset();
                m_hashTable.reset(new std::uint32_t[(m_poolSize + 1) * 2]);
                clear();
            }

            bool MatchEnabled() const { return bool(m_matchTable); }
            void EnableMatch(bool enabled) {
                if (enabled==MatchEnabled()) return;
                if (enabled) {
                    m_hashTable.reset();
                    m_matchTable.reset(new std::uint64_t[std::size_t(m_poolSize+1)*2]());
                } else {
                    m_matchTable.reset();
                    m_hashTable.reset(new std::uint32_t[std::size_t(m_poolSize+1)*2]());
                }
                m_secondHash=false;
            }

            void clear()
            {
                if (m_matchTable) {
                    memset(m_matchTable.get(),0,sizeof(std::uint64_t)*(m_poolSize+1)*(m_secondHash?2:1));
                    m_secondHash=false;
                    return;
                }
                if (!m_secondHash)
                {
                    // Clear first block.
                    memset(m_hashTable.get(), 0, sizeof(SizeType) * (m_poolSize + 1));
                }
                else
                {
                    // Clear all blocks.
                    m_secondHash = false;
                    memset(m_hashTable.get(), 0, 2 * sizeof(SizeType) * (m_poolSize + 1));
                }
            }

            inline int HashTableExponent() const { return m_exp; }

            inline int MaxCheck() const { return (1 << (int)(log2(m_poolSize + 1) - m_exp)); }

            inline bool CheckAndSet(SizeType idx)
            {
                if (m_matchTable) throw std::logic_error("Uninitialized visited match insertion");
                return Probe(m_hashTable,idx,[](SizeType){return false;},false).first;
            }

            template<class Predicate>
            std::pair<bool,bool> CheckAndSetMatch(SizeType id,Predicate&& predicate,bool rejectNegative=false) {
                if (!m_matchTable) throw std::logic_error("Visited match mode is disabled");
                return Probe(m_matchTable,id,std::forward<Predicate>(predicate),rejectNegative);
            }

            inline bool Contains(SizeType idx) const
            {
                const auto key=Key(idx);
                if (m_matchTable) {
                    auto* slot=Find(m_matchTable.get(),m_poolSize,key);
                    if (slot && *slot) return true;
                    slot=m_secondHash?Find(m_matchTable.get()+m_poolSize+1,m_poolSize,key):nullptr;
                    return slot && *slot;
                }
                if (!m_hashTable) return false;
                if (_Contains(m_hashTable.get(), m_poolSize, key)) return true;
                return m_secondHash &&
                    _Contains(m_hashTable.get() + m_poolSize + 1, m_poolSize, key);
            }

            inline void DoubleSize()
            {
                if (m_matchTable) Grow(m_matchTable);
                else Grow(m_hashTable);
            }

            inline bool _Contains(const std::uint32_t* hashTable, int poolSize, std::uint32_t idx) const
            {
                unsigned index = hash_func((unsigned)idx, poolSize);
                for (int loop = 0; loop < m_maxLoop; ++loop)
                {
                    if (!hashTable[index]) return false;
                    if (hashTable[index] == idx) return true;
                    index = hash_func2(index, poolSize, loop);
                }
                return false;
            }
        };

        class DistPriorityQueue {
            int m_size;
            std::unique_ptr<float[]> m_data;
            int m_length;
            int m_count;
            
        public:
            DistPriorityQueue(): m_size(0), m_length(0), m_count(0) {}

            ~DistPriorityQueue() { m_data.reset(); }

            void Resize(int size_) {
                m_size = size_;
                m_data.reset(new float[size_ + 1]);
                
                m_data[1] = MaxDist;
                m_length = 1;
                m_count = size_;
            }
            void clear(int count_) {
                if (count_ > m_size) {
                    m_size = count_;
                    m_data.reset(new float[count_ + 1]);
                }
                m_data[1] = MaxDist;
                m_length = 1;
                m_count = count_;
                
            }
            bool insert(float dist) {
                if (dist > m_data[1]) return false;

                if (m_length == m_count) {
                    m_data[1] = dist;
                    int parent = 1, next = 2;
                    while (next < m_length) {
                        if (m_data[next] < m_data[next + 1]) next++;
                        if (m_data[next] > m_data[parent]) {
                            std::swap(m_data[parent], m_data[next]);
                            parent = next;
                            next <<= 1;
                        }
                        else break;
                    }
                    if (next == m_length && m_data[next] > m_data[parent]) std::swap(m_data[parent], m_data[next]);
                }
                else {
                    int next = ++(m_length), parent = (next >> 1);
                    while (parent > 0 && dist > m_data[parent]) {
                        m_data[next] = m_data[parent];
                        next = parent;
                        parent >>= 1;
                    }
                    m_data[next] = dist;
                }
                return true;
            }
            inline float worst() {
                return m_data[1];
            }
        };

        class IWorkSpace {};

        // Variables for each single NN search
        struct WorkSpace : public IWorkSpace
        {
            WorkSpace() {}

            WorkSpace(WorkSpace& other) 
            {
                Initialize(other.m_iMaxCheck, other.nodeCheckStatus.HashTableExponent());
            }

            ~WorkSpace() {
                //SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Delete workspace happens!\n");
            }

            void Initialize(int maxCheck, int hashExp)
            {
                m_nativeHooks=nullptr;
                m_matchPredicate={};
                nodeCheckStatus.Init(maxCheck, hashExp);
                m_SPTQueue.Resize(maxCheck * 10);
                m_NGQueue.Resize(maxCheck * 30);
                m_Results.Resize(maxCheck / 16);

                m_iNumOfContinuousNoBetterPropagation = 0;
                //m_iContinuousLimit = maxCheck / 64;
                m_iNumberOfTreeCheckedLeaves = 0;
                m_iNumberOfCheckedLeaves = 0;
                m_iMaxCheck = maxCheck;
                m_relaxedMono = false;
            }

            void Initialize(va_list& arg)
            {
                int maxCheck = va_arg(arg, int);
                int hashExp = va_arg(arg, int);
                Initialize(maxCheck, hashExp);
            }

            void Reset(int maxCheck, int resultNum)
            {
                m_nativeHooks = nullptr;
                m_matchPredicate={};
                nodeCheckStatus.EnableMatch(false);
                nodeCheckStatus.clear();
                m_SPTQueue.clear(maxCheck * 10);
                m_NGQueue.clear(maxCheck * 30);
                m_Results.clear(max(maxCheck / 16, resultNum));

                m_iNumOfContinuousNoBetterPropagation = 0;
                //m_iContinuousLimit = maxCheck / 64;
                m_iNumberOfTreeCheckedLeaves = 0;
                m_iNumberOfCheckedLeaves = 0;
                m_iMaxCheck = maxCheck;
                m_relaxedMono = false;
            }

            void PrepareResultCheckStatus()
            {
                if (!m_resultCheckInitialized ||
                    resultCheckStatus.MaxCheck() <
                        m_iMaxCheck)
                {
                    resultCheckStatus.Init(
                        (std::max)(16, m_iMaxCheck),
                        nodeCheckStatus
                            .HashTableExponent());
                    m_resultCheckInitialized = true;
                }
                else
                {
                    resultCheckStatus.clear();
                }
            }

            void ResetResult(int maxCheck, int resultNum)
            {
                m_Results.clear(max(maxCheck / 16, resultNum));
                m_iNumOfContinuousNoBetterPropagation = 0;
                m_iNumberOfTreeCheckedLeaves = 0;
                m_iNumberOfCheckedLeaves = 0;
            }

            inline bool CheckAndSet(SizeType idx)
            {
                if (nodeCheckStatus.MatchEnabled()) return CheckAndSetMatch(idx).first;
                const bool visited=nodeCheckStatus.CheckAndSet(idx);
                if (m_nativeHooks && m_nativeHooks->diagnostics) ++m_nativeHooks->visitedProbes;
                if (m_nativeHooks && m_nativeHooks->auditVisited)
                    m_nativeHooks->allVisited.emplace_back(idx,visited);
                return visited;
            }

            std::pair<bool,bool> CheckAndSetMatch(SizeType id,bool rejectNegative=false) {
                if (!m_matchPredicate) throw std::logic_error("Missing physical visited predicate");
                auto predicate=[&](SizeType candidate) {
                    const bool match=m_matchPredicate(candidate);
                    if (m_nativeHooks && m_nativeHooks->diagnostics) {
                        ++m_nativeHooks->matchEvaluations;
                        if (!rejectNegative || match) ++m_nativeHooks->firstMatchEvaluations;
                        else ++m_nativeHooks->rejectedAuxiliaryEvaluations;
                    }
                    return match;
                };
                const auto result=nodeCheckStatus.CheckAndSetMatch(id,predicate,rejectNegative);
                if (m_nativeHooks) {
                    if (m_nativeHooks->diagnostics) {
                        ++m_nativeHooks->visitedProbes;
                        m_nativeHooks->reusedMatchReads+=result.first;
                    }
                    if (m_nativeHooks->auditVisited && (!rejectNegative || result.first || result.second))
                        m_nativeHooks->allVisited.emplace_back(id,result.first);
                }
                return result;
            }

            inline bool CheckResultAndSet(SizeType idx)
            {
                if (!m_resultCheckInitialized)
                {
                    PrepareResultCheckStatus();
                }
                return resultCheckStatus.CheckAndSet(idx);
            }

            inline bool Contains(SizeType idx) const
            {
                return nodeCheckStatus.Contains(idx);
            }

            inline int HashTableExponent() const 
            { 
                return nodeCheckStatus.HashTableExponent(); 
            }

            static void Reset() {}

            NativeNeighborHooks* m_nativeHooks = nullptr;
            std::function<bool(SizeType)> m_matchPredicate;
            OptHashPosVector nodeCheckStatus;
            OptHashPosVector resultCheckStatus;
            bool m_resultCheckInitialized = false;

            // counter for dynamic pivoting
            int m_iNumOfContinuousNoBetterPropagation = 0;
            int m_iContinuousLimit = 128;
            int m_iNumberOfTreeCheckedLeaves = 0;
            int m_iNumberOfCheckedLeaves = 0;
            int m_iMaxCheck = 8192;
            bool m_relaxedMono = false;

            // Prioriy queue used for neighborhood graph
            Heap<NodeDistPair> m_NGQueue;

            // Priority queue Used for Tree
            Heap<NodeDistPair> m_SPTQueue;
            // Priority queue Used for Tree BFS
            Heap<NodeDistPair> m_currBSPTQueue;
            Heap<NodeDistPair> m_nextBSPTQueue;

            DistPriorityQueue m_Results;
            std::function<bool(const ByteArray&)> m_filterFunc;
        };
    }
}

#endif // _SPTAG_COMMON_WORKSPACE_H_
