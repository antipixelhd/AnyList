export function gameProgress(state:Record<string,any>|null|undefined) {
  const hours=state?.total_seconds==null?null:`${new Intl.NumberFormat('en',{maximumFractionDigits:1}).format(state.total_seconds/3600)}h`;
  const progress=state?.achievements;
  const achievement=!progress?.mode?null:progress.count!=null&&progress.total!=null
    ? `${progress.count} / ${progress.total}`
    : progress.percentage!=null?`${Number(progress.percentage.toFixed(1))}%`:null;
  return {hours,achievement};
}
