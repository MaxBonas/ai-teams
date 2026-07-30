interface ProviderSummary {
  profile_id: string;
  provider: string;
  channel: string;
  model_count: number;
  configured_count: number;
  green_count: number;
  selectable_count: number;
  blocked_count: number;
  data_policy?: string | null;
  economy_classes?: string[];
}

function humanize(value?: string | null): string {
  if (!value) return 'No declarado';
  return value.replaceAll('_', ' ').replace(/^./, (char) => char.toUpperCase());
}

export function ProviderCard({ provider }: { provider: ProviderSummary }) {
  const allGreen = provider.model_count > 0 && provider.green_count === provider.model_count;
  const economy = provider.economy_classes?.[0];
  return (
    <article className={`model-provider-card ${allGreen ? 'is-green' : ''}`} data-testid={`provider-${provider.profile_id}`}>
      <header>
        <span
          className={`provider-pulse ${allGreen ? 'is-green' : ''}`}
          role="img"
          aria-label={allGreen ? 'Adapter verde' : 'Adapter no completamente verde'}
        />
        <div>
          <strong>{humanize(provider.provider)}</strong>
          <small>{provider.profile_id}</small>
        </div>
        <span className="channel-stamp">{humanize(provider.channel)}</span>
      </header>
      <div className="provider-card-counts">
        <span><strong>{provider.model_count}</strong> modelos</span>
        <span><strong>{provider.configured_count}</strong> configurados</span>
        <span><strong>{provider.green_count}</strong> verdes</span>
        <span><strong>{provider.selectable_count}</strong> seleccionables</span>
        <span className={provider.blocked_count ? 'has-blocked' : ''}><strong>{provider.blocked_count}</strong> bloqueados</span>
      </div>
      <dl>
        <div><dt>Economía</dt><dd>{humanize(economy)}</dd></div>
        <div><dt>Datos</dt><dd>{humanize(provider.data_policy)}</dd></div>
      </dl>
    </article>
  );
}
