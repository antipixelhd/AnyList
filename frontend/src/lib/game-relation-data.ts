type RelatedWork = {
  id?: number;
  type: string;
  title: string;
  url: string;
  relation?: string;
  poster?: string | null;
  year?: string | null;
};

type RelationCard<T> = {work: T; gameIds: number[]};

function relationPriority(relation?: string) {
  if (!relation || relation === 'series_member' || relation === 'recommendation') return 0;
  return relation === 'version' ? 1 : 2;
}

export function gameRelationCards<T extends RelatedWork>(relations: T[]): RelationCard<T>[] {
  const byWork = new Map<string, RelationCard<T>>();
  for (const work of relations) {
    const gameId = work.type === 'game' && Number.isSafeInteger(work.id) && work.id! > 0 ? work.id : undefined;
    const key = gameId ? `game:${gameId}` : work.url;
    const existing = byWork.get(key);
    if (!existing || relationPriority(work.relation) > relationPriority(existing.work.relation)) {
      byWork.set(key, {work, gameIds: gameId ? [gameId] : []});
    }
  }

  const ports = new Map<string, RelationCard<T>>();
  const cards: RelationCard<T>[] = [];
  for (const card of byWork.values()) {
    const {work} = card;
    const port = work.type === 'game' && work.relation === 'port'
      ? work.title.normalize('NFKC').trim().replace(/\s+/g,' ').toLowerCase() : '';
    const existing = port ? ports.get(port) : undefined;
    if (existing) {
      existing.gameIds.push(...card.gameIds);
      if (Number(!!work.poster) + Number(!!work.year) > Number(!!existing.work.poster) + Number(!!existing.work.year)) {
        existing.work = work;
      }
    } else {
      if (port) ports.set(port,card);
      cards.push(card);
    }
  }
  return cards;
}
