import { useState, useEffect, useCallback, useRef } from 'react';
import { MKMember } from '../types';

interface ImagesState {
  lowRes: Map<string, string>;
  highRes: Map<string, string>;
  aliases: Map<string, string>;
}

export function useWikipediaImages(roster: MKMember[]) {
  const [imagesState, setImagesState] = useState<ImagesState>({
    lowRes: new Map(),
    highRes: new Map(),
    aliases: new Map(),
  });
  const fetchedTitlesRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!roster.length) return;

    const uniqueTitles = [...new Set(roster.map((member) => member.wikiTitle))].filter(
      (title) => title && !fetchedTitlesRef.current.has(title)
    );

    if (!uniqueTitles.length) return;

    uniqueTitles.forEach((t) => fetchedTitlesRef.current.add(t));

    let isMounted = true;
    const chunkSize = 25;

    function buildThumbnailUrl(titles: string[], thumbSize: number): URL {
      const url = new URL('https://he.wikipedia.org/w/api.php');
      url.searchParams.set('action', 'query');
      url.searchParams.set('format', 'json');
      url.searchParams.set('origin', '*');
      url.searchParams.set('redirects', '1');
      url.searchParams.set('prop', 'pageimages');
      url.searchParams.set('piprop', 'thumbnail');
      url.searchParams.set('pithumbsize', String(thumbSize));
      url.searchParams.set('titles', titles.join('|'));
      return url;
    }

    function collectThumbnails(data: {
      query?: { pages?: Record<string, unknown> };
    }): Map<string, string> {
      const thumbnails = new Map<string, string>();
      Object.values(data.query?.pages || {}).forEach((page: unknown) => {
        const p = page as { title: string; thumbnail?: { source: string } };
        if (p.thumbnail?.source) {
          thumbnails.set(p.title, p.thumbnail.source);
        }
      });
      return thumbnails;
    }

    async function loadChunks() {
      for (let index = 0; index < uniqueTitles.length; index += chunkSize) {
        const chunk = uniqueTitles.slice(index, index + chunkSize);

        try {
          // Two independent requests: Wikimedia's thumbnail service only
          // serves a fixed set of pre-generated widths (e.g. 120/250/500),
          // so a low-res URL can't be derived by editing the high-res URL's
          // width segment (that produces a width Wikimedia never generated,
          // which 400s). Asking the API for each size separately lets it
          // snap to the nearest valid width itself, the same way it already
          // does for the high-res request.
          const [lowResResponse, highResResponse] = await Promise.all([
            fetch(buildThumbnailUrl(chunk, 150)),
            fetch(buildThumbnailUrl(chunk, 600)),
          ]);
          if (!lowResResponse.ok || !highResResponse.ok) continue;

          const [lowData, highData] = await Promise.all([
            lowResResponse.json(),
            highResResponse.json(),
          ]);
          if (!isMounted) return;

          const newAliases = new Map<string, string>();
          (highData.query?.normalized || []).forEach((item: { from: string; to: string }) => {
            newAliases.set(item.from, item.to);
          });
          (highData.query?.redirects || []).forEach((item: { from: string; to: string }) => {
            newAliases.set(item.from, item.to);
          });

          const newLowRes = collectThumbnails(lowData);
          const newHighRes = collectThumbnails(highData);

          if (newAliases.size > 0 || newLowRes.size > 0 || newHighRes.size > 0) {
            setImagesState((prev) => ({
              aliases: newAliases.size > 0 ? new Map([...prev.aliases, ...newAliases]) : prev.aliases,
              lowRes: newLowRes.size > 0 ? new Map([...prev.lowRes, ...newLowRes]) : prev.lowRes,
              highRes: newHighRes.size > 0 ? new Map([...prev.highRes, ...newHighRes]) : prev.highRes,
            }));
          }
        } catch {
          // Initials remain visible if remote images fail
        }
      }
    }

    loadChunks();

    return () => {
      isMounted = false;
    };
  }, [roster]);

  const resolveImage = useCallback(
    (title: string): string => {
      let resolved = title;
      const visited = new Set<string>();

      while (imagesState.aliases.has(resolved) && !visited.has(resolved)) {
        visited.add(resolved);
        resolved = imagesState.aliases.get(resolved)!;
      }

      return imagesState.lowRes.get(resolved) || imagesState.lowRes.get(title) || '';
    },
    [imagesState]
  );

  const resolveHighResImage = useCallback(
    (title?: string): string => {
      if (!title) return '';
      let resolved = title;
      const visited = new Set<string>();

      while (imagesState.aliases.has(resolved) && !visited.has(resolved)) {
        visited.add(resolved);
        resolved = imagesState.aliases.get(resolved)!;
      }

      return imagesState.highRes.get(resolved) || imagesState.highRes.get(title) || '';
    },
    [imagesState]
  );

  const imageFor = useCallback(
    (member: MKMember): string => {
      if (member.imageUrl) return member.imageUrl;
      return resolveImage(member.wikiTitle);
    },
    [resolveImage]
  );

  return { imageFor, resolveImage, resolveHighResImage };
}

