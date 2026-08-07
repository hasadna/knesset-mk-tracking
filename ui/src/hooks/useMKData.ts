import { useState, useEffect, useMemo } from 'react';
import { MKMember, Topic } from '../types';
import { PARTY_PALETTE } from '../utils/formatters';

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path}: HTTP ${response.status}`);
  }
  return response.json();
}

export function useMKData() {
  const [roster, setRoster] = useState<MKMember[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    async function load() {
      try {
        const [loadedRoster, loadedTopics] = await Promise.all([
          fetchJson<MKMember[]>('/api/mks'),
          fetchJson<Topic[]>('/api/issues'),
        ]);

        if (!Array.isArray(loadedRoster) || !loadedRoster.length) {
          throw new Error('/api/mks אינו מכיל רשימה תקינה');
        }
        if (!Array.isArray(loadedTopics) || !loadedTopics.length) {
          throw new Error('/api/issues אינו מכיל רשימה תקינה');
        }

        if (isMounted) {
          setRoster(loadedRoster);
          setTopics(loadedTopics);
          setLoading(false);
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      isMounted = false;
    };
  }, []);

  const partyInfo = useMemo(() => {
    return Object.fromEntries(
      roster.map((member) => [
        member.party,
        { seats: member.seats, status: member.bloc },
      ])
    );
  }, [roster]);

  const partyColors = useMemo(() => {
    const partyNames = Object.keys(partyInfo);
    const map = new Map<string, string>(
      partyNames.map((name, index) => [
        name,
        PARTY_PALETTE[index % PARTY_PALETTE.length],
      ])
    );
    map.set('לא חברי כנסת', '#607d8b');
    map.set('ישר! עם איזנקוט', '#4c6d86');
    return map;
  }, [partyInfo]);

  const topicMap = useMemo(() => {
    return new Map<string, Topic>(topics.map((t) => [t.id, t]));
  }, [topics]);

  return { roster, topics, partyInfo, partyColors, topicMap, loading, error };
}
