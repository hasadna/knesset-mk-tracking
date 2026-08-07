import { useState, useCallback, useRef } from 'react';
import { MKMember, MKAnalysis, TopicDetail, TweetPost } from '../types';

const MAX_LOADED_MEMBERS = 25;

export function useMKAnalysis() {
  const [analysisMap, setAnalysisMap] = useState<Map<string, Map<string, TopicDetail>>>(new Map());
  const [postMap, setPostMap] = useState<Map<string, TweetPost>>(new Map());
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const pendingPromises = useRef<Map<string, Promise<void>>>(new Map());
  const loadedMembers = useRef<Set<string>>(new Set());
  const loadedOrderRef = useRef<string[]>([]);
  const memberPostKeysRef = useRef<Map<string, Set<string>>>(new Map());

  const touchRecency = useCallback((key: string) => {
    loadedOrderRef.current = [...loadedOrderRef.current.filter((k) => k !== key), key];
  }, []);

  const loadEvidence = useCallback(async (member: MKMember): Promise<void> => {
    if (loadedMembers.current.has(member.key)) {
      // Cache hit: refresh recency so actively revisited members aren't evicted
      // in favor of ones that were only fetched once, long ago.
      touchRecency(member.key);
      return;
    }
    if (pendingPromises.current.has(member.key)) {
      return pendingPromises.current.get(member.key)!;
    }

    setLoading(true);
    setError(null);

    const promise = (async () => {
      try {
        const encodedKey = encodeURIComponent(member.key);
        const response = await fetch(`/api/mks/${encodedKey}/issues`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const loadedAnalysis: MKAnalysis = await response.json();

        if (!Array.isArray(loadedAnalysis.topics) || !Array.isArray(loadedAnalysis.sourcePosts)) {
          throw new Error('תגובת ניתוח העמדות אינה תקינה');
        }

        const newTopicsMap = new Map<string, TopicDetail>(
          loadedAnalysis.topics.map((opinion) => [opinion.id, opinion])
        );

        // LRU Eviction if max capacity exceeded
        loadedMembers.current.add(member.key);
        touchRecency(member.key);
        memberPostKeysRef.current.set(member.key, new Set(loadedAnalysis.sourcePosts.map((post) => post.key)));

        let evictedKey: string | null = null;
        if (loadedOrderRef.current.length > MAX_LOADED_MEMBERS) {
          evictedKey = loadedOrderRef.current.shift()!;
          loadedMembers.current.delete(evictedKey);
        }

        // A post can in principle be cited by more than one member, so only prune
        // an evicted member's posts once no remaining loaded member still owns them.
        let postsToPrune: string[] = [];
        if (evictedKey) {
          const ownedKeys = memberPostKeysRef.current.get(evictedKey);
          memberPostKeysRef.current.delete(evictedKey);
          if (ownedKeys) {
            const stillOwned = new Set<string>();
            memberPostKeysRef.current.forEach((keys) => keys.forEach((k) => stillOwned.add(k)));
            postsToPrune = [...ownedKeys].filter((k) => !stillOwned.has(k));
          }
        }

        setAnalysisMap((prev) => {
          const updated = new Map(prev);
          if (evictedKey) {
            updated.delete(evictedKey);
          }
          updated.set(member.key, newTopicsMap);
          return updated;
        });

        setPostMap((prev) => {
          const updated = new Map(prev);
          loadedAnalysis.sourcePosts.forEach((post) => updated.set(post.key, post));
          postsToPrune.forEach((key) => updated.delete(key));
          return updated;
        });
      } catch (err) {
        pendingPromises.current.delete(member.key);
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
        throw err;
      } finally {
        pendingPromises.current.delete(member.key);
        setLoading(false);
      }
    })();

    pendingPromises.current.set(member.key, promise);
    return promise;
  }, [touchRecency]);

  const getTopicDetail = useCallback(
    (memberKey: string, topicId: string): TopicDetail => {
      const memberAnalysis = analysisMap.get(memberKey);
      if (memberAnalysis?.has(topicId)) {
        return memberAnalysis.get(topicId)!;
      }
      return {
        id: topicId,
        status: 'none',
        postCount: 0,
        stance: '',
        extendedStance: '',
        sources: [],
      };
    },
    [analysisMap]
  );

  return { loadEvidence, getTopicDetail, analysisMap, postMap, loading, error };
}
