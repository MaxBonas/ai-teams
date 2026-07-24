import { useCallback, useState, type Dispatch, type SetStateAction } from 'react';
import { apiFetch } from '../lib/api';
import type {
  AdapterProfile,
  CliStatus,
  McpCatalogEntry,
  McpServer,
  McpToolDrafts,
  ProjectSkill,
  SecretInfo,
  SkillDraft,
  SkillGovernance,
} from '../components/ConfigurationPanel/types';

interface ConfigurationDataOptions {
  setGlobalBusy: (busy: boolean) => void;
  reportError: (message: string) => void;
  reportResult: (result: unknown) => void;
  setSelectedProjectAdapterIds: Dispatch<SetStateAction<string[]>>;
}

export function useConfigurationData({
  setGlobalBusy,
  reportError,
  reportResult,
  setSelectedProjectAdapterIds,
}: ConfigurationDataOptions) {
  const [projectsRoot, setProjectsRoot] = useState('');
  const [settingsConfigured, setSettingsConfigured] = useState(true);
  const [settingsDraft, setSettingsDraft] = useState('');
  const [adapterProfiles, setAdapterProfiles] = useState<AdapterProfile[]>([]);
  const [adapterTestModels, setAdapterTestModels] = useState<Record<string, string>>({});
  const [cliStatus, setCliStatus] = useState<CliStatus[]>([]);
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [secretProvider, setSecretProvider] = useState('openai');
  const [secretValue, setSecretValue] = useState('');
  const [autonomyMode, setAutonomyMode] = useState('supervised');
  const [autonomySaving, setAutonomySaving] = useState(false);
  const [projectSkills, setProjectSkills] = useState<ProjectSkill[]>([]);
  const [skillGovernance, setSkillGovernance] = useState<SkillGovernance | null>(null);
  const [skillDraft, setSkillDraft] = useState<SkillDraft>({
    name: '',
    roles: '',
    body: '',
    status: 'active',
  });
  const [skillSaving, setSkillSaving] = useState(false);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [mcpCatalog, setMcpCatalog] = useState<McpCatalogEntry[]>([]);
  const [mcpBusy, setMcpBusy] = useState('');
  const [mcpToolDrafts, setMcpToolDrafts] = useState<McpToolDrafts>({});

  const profileState = (profile: AdapterProfile) => {
    const provider = String(profile.provider || '').toLowerCase();
    const secretProviderId = provider.includes('google') || provider.includes('gemini')
      ? 'google'
      : provider.includes('anthropic') || provider.includes('claude')
        ? 'anthropic'
        : provider.includes('openai') || provider.includes('codex')
          ? 'openai'
          : provider;
    const hasSecret = Boolean(
      secretProviderId
      && secrets.some((secret) => secret.provider === secretProviderId && secret.has_secret),
    );
    const healthStatus = String(profile.health?.status || 'untested');
    const connected = healthStatus === 'ok' || (profile.channel === 'api' && hasSecret);
    const selectable = profile.status !== 'blocked_by_provider' && connected;
    const label = connected
      ? (healthStatus === 'ok' ? 'conectado y probado' : 'API key guardada')
      : healthStatus === 'installed'
        ? 'CLI instalado; login sin verificar'
        : profile.status === 'blocked_by_provider'
          ? 'bloqueado por proveedor'
          : profile.channel === 'api'
            ? 'falta API key'
            : 'sin conectar';
    return { connected, selectable, label, secretProvider: secretProviderId };
  };

  const loadAppSettings = async () => {
    try {
      const response = await apiFetch('/api/settings');
      if (!response.ok) return;
      const data = (await response.json()) as {
        configured?: boolean;
        projects_root?: string;
        projects_root_effective?: string;
      };
      setSettingsConfigured(Boolean(data.configured));
      setProjectsRoot(data.projects_root_effective || data.projects_root || '');
      setSettingsDraft((current) => current || data.projects_root || '');
    } catch {
      setSettingsConfigured(true);
    }
  };

  const saveAppSettings = async () => {
    setGlobalBusy(true);
    reportError('');
    try {
      const response = await apiFetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ projects_root: settingsDraft.trim() }),
      });
      if (!response.ok) {
        const error = (await response.json()) as { detail?: string };
        throw new Error(error.detail || `settings:${response.status}`);
      }
      const data = (await response.json()) as {
        configured?: boolean;
        projects_root_effective?: string;
      };
      setSettingsConfigured(Boolean(data.configured));
      setProjectsRoot(data.projects_root_effective || settingsDraft.trim());
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'save_settings_failed');
    } finally {
      setGlobalBusy(false);
    }
  };

  const loadUserAdapters = async () => {
    try {
      const response = await apiFetch('/api/user-adapters');
      if (!response.ok) return;
      const data = (await response.json()) as {
        profiles?: AdapterProfile[];
        cli_status?: CliStatus[];
        secrets?: SecretInfo[];
      };
      const profiles = data.profiles || [];
      const nextSecrets = data.secrets || [];
      setAdapterProfiles(profiles);
      setSelectedProjectAdapterIds((current) => {
        if (current.length > 0) return current;
        return profiles
          .filter((profile) => profile.status !== 'blocked_by_provider')
          .filter((profile) => {
            const provider = String(profile.provider || '').toLowerCase();
            const providerId = provider.includes('google') || provider.includes('gemini')
              ? 'google'
              : provider.includes('anthropic') || provider.includes('claude')
                ? 'anthropic'
                : provider.includes('openai') || provider.includes('codex')
                  ? 'openai'
                  : provider;
            const hasSecret = nextSecrets.some(
              (secret) => secret.provider === providerId && secret.has_secret,
            );
            return String(profile.health?.status || '') === 'ok'
              || (profile.channel === 'api' && hasSecret);
          })
          .slice(0, 2)
          .map((profile) => profile.id);
      });
      setCliStatus(data.cli_status || []);
      setSecrets(nextSecrets);
    } catch {
      // La configuración local es auxiliar y no bloquea el cockpit.
    }
  };

  const saveAutonomy = async (mode: string) => {
    if (mode === autonomyMode || autonomySaving) return;
    setAutonomySaving(true);
    const previous = autonomyMode;
    setAutonomyMode(mode);
    try {
      const response = await apiFetch('/api/project/autonomy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      const data = (await response.json()) as {
        success?: boolean;
        autonomy?: string;
        detail?: string;
      };
      if (!response.ok || !data.success) {
        throw new Error(data.detail || `autonomy:${response.status}`);
      }
      setAutonomyMode(data.autonomy || mode);
    } catch {
      setAutonomyMode(previous);
    } finally {
      setAutonomySaving(false);
    }
  };

  const loadProjectSkills = useCallback(async () => {
    try {
      const response = await apiFetch('/api/project/skills');
      if (!response.ok) return;
      const data = (await response.json()) as {
        skills?: ProjectSkill[];
        governance?: SkillGovernance;
      };
      setProjectSkills(data.skills || []);
      setSkillGovernance(data.governance || null);
    } catch {
      // Las skills se recargan en el siguiente refresh.
    }
  }, []);

  const loadMcpServers = useCallback(async () => {
    try {
      const [response, catalogResponse] = await Promise.all([
        apiFetch('/api/project/extensions/mcp'),
        apiFetch('/api/project/extensions/mcp/catalog'),
      ]);
      if (!response.ok) return;
      const data = (await response.json()) as { mcp_servers?: McpServer[] };
      const servers = data.mcp_servers || [];
      setMcpServers(servers);
      if (catalogResponse.ok) {
        const catalogData = (await catalogResponse.json()) as { entries?: McpCatalogEntry[] };
        setMcpCatalog(catalogData.entries || []);
      }
      setMcpToolDrafts((current) => {
        const next = { ...current };
        for (const server of servers) {
          const approved = new Map(
            (server.approved_tools || []).map((tool) => [tool.name, tool.access]),
          );
          next[server.name] = Object.fromEntries(
            (server.health?.tools || []).map(
              (tool) => [tool.name, approved.get(tool.name) || 'off'],
            ),
          );
        }
        return next;
      });
    } catch {
      // Las extensiones se recargan en el siguiente refresh.
    }
  }, []);

  const runMcpHealth = async (server: McpServer) => {
    if (mcpBusy) return;
    setMcpBusy(server.name);
    reportError('');
    try {
      const response = await apiFetch(
        `/api/project/extensions/mcp/${encodeURIComponent(server.name)}/health`,
        { method: 'POST' },
      );
      const data = (await response.json()) as {
        success?: boolean;
        detail?: string;
        mcp_server?: McpServer;
      };
      if (!response.ok) throw new Error(data.detail || `mcp_health:${response.status}`);
      if (!data.success) {
        reportError(data.mcp_server?.health?.detail || 'El servidor no superó el health check.');
      }
      await loadMcpServers();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'mcp_health_failed');
    } finally {
      setMcpBusy('');
    }
  };

  const saveMcpToolPolicy = async (server: McpServer) => {
    if (mcpBusy) return;
    const tools = Object.entries(mcpToolDrafts[server.name] || {})
      .filter(([, access]) => access !== 'off')
      .map(([name, access]) => ({ name, access }));
    if (tools.length === 0) {
      reportError('Aprueba al menos una herramienta o retira el servidor.');
      return;
    }
    setMcpBusy(server.name);
    reportError('');
    try {
      const response = await apiFetch(
        `/api/project/extensions/mcp/${encodeURIComponent(server.name)}/tools`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tools }),
        },
      );
      const data = (await response.json()) as { success?: boolean; detail?: string };
      if (!response.ok || !data.success) {
        throw new Error(data.detail || `mcp_tools:${response.status}`);
      }
      await loadMcpServers();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'mcp_tools_failed');
    } finally {
      setMcpBusy('');
    }
  };

  const transitionMcpServer = async (
    server: McpServer,
    action: 'retire' | 'reactivate',
  ) => {
    if (mcpBusy) return;
    setMcpBusy(server.name);
    reportError('');
    try {
      const response = await apiFetch(
        `/api/project/extensions/mcp/${encodeURIComponent(server.name)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action }),
        },
      );
      const data = (await response.json()) as { success?: boolean; detail?: string };
      if (!response.ok || !data.success) {
        throw new Error(data.detail || `mcp_lifecycle:${response.status}`);
      }
      await loadMcpServers();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'mcp_lifecycle_failed');
    } finally {
      setMcpBusy('');
    }
  };

  const saveProjectSkill = async () => {
    const name = skillDraft.name.trim();
    const body = skillDraft.body.trim();
    if (!name || !body || skillSaving) return;
    setSkillSaving(true);
    try {
      const roles = skillDraft.roles.split(',').map((role) => role.trim()).filter(Boolean);
      const response = await apiFetch('/api/project/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          body,
          applies_to_roles: roles,
          status: skillDraft.status,
        }),
      });
      const data = (await response.json()) as { success?: boolean; detail?: string };
      if (!response.ok || !data.success) {
        throw new Error(data.detail || `skill:${response.status}`);
      }
      setSkillDraft({ name: '', roles: '', body: '', status: 'active' });
      await loadProjectSkills();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'skill_save_failed');
    } finally {
      setSkillSaving(false);
    }
  };

  const editProjectSkill = (skill: ProjectSkill) => {
    setSkillDraft({
      name: skill.name,
      roles: (skill.applies_to_roles || []).join(', '),
      body: skill.body || '',
      status: skill.status || 'active',
    });
  };

  const toggleProjectSkill = async (skill: ProjectSkill) => {
    const status = skill.status === 'active' ? 'retired' : 'active';
    try {
      const response = await apiFetch(`/api/project/skills/${encodeURIComponent(skill.name)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      const data = (await response.json()) as { success?: boolean; detail?: string };
      if (!response.ok || !data.success) {
        throw new Error(data.detail || `skill_status:${response.status}`);
      }
      await loadProjectSkills();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'skill_status_failed');
    }
  };

  const deleteProjectSkill = async (skill: ProjectSkill) => {
    try {
      const response = await apiFetch(
        `/api/project/skills/${encodeURIComponent(skill.name)}`,
        { method: 'DELETE' },
      );
      const data = (await response.json()) as { success?: boolean; detail?: string };
      if (!response.ok || !data.success) {
        throw new Error(data.detail || `skill_delete:${response.status}`);
      }
      await loadProjectSkills();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'skill_delete_failed');
    }
  };

  const saveSecret = async () => {
    if (!secretValue.trim()) return;
    setGlobalBusy(true);
    reportError('');
    try {
      const response = await apiFetch('/api/user-adapters/secrets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: secretProvider,
          name: 'default',
          secret: secretValue.trim(),
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `secret:${response.status}`);
      setSecretValue('');
      reportResult(data);
      await loadUserAdapters();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'secret_save_failed');
    } finally {
      setGlobalBusy(false);
    }
  };

  const launchCliLogin = async (cliId: string) => {
    setGlobalBusy(true);
    reportError('');
    try {
      const response = await apiFetch('/api/user-adapters/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cli_id: cliId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `login:${response.status}`);
      reportResult(data);
      await loadUserAdapters();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'subscription_login_failed');
    } finally {
      setGlobalBusy(false);
    }
  };

  const testAdapterProfile = async (profileId: string, model?: string) => {
    setGlobalBusy(true);
    reportError('');
    try {
      const response = await apiFetch('/api/user-adapters/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profileId, ...(model ? { model } : {}) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `adapter-test:${response.status}`);
      reportResult(data);
      await loadUserAdapters();
    } catch (error) {
      reportError(error instanceof Error ? error.message : 'adapter_test_failed');
    } finally {
      setGlobalBusy(false);
    }
  };

  return {
    projectsRoot,
    setProjectsRoot,
    settingsConfigured,
    settingsDraft,
    setSettingsDraft,
    adapterProfiles,
    adapterTestModels,
    setAdapterTestModels,
    cliStatus,
    secrets,
    secretProvider,
    setSecretProvider,
    secretValue,
    setSecretValue,
    autonomyMode,
    setAutonomyMode,
    autonomySaving,
    projectSkills,
    skillGovernance,
    skillDraft,
    setSkillDraft,
    skillSaving,
    mcpServers,
    mcpCatalog,
    mcpBusy,
    mcpToolDrafts,
    setMcpToolDrafts,
    profileState,
    loadAppSettings,
    saveAppSettings,
    loadUserAdapters,
    saveAutonomy,
    loadProjectSkills,
    loadMcpServers,
    runMcpHealth,
    saveMcpToolPolicy,
    transitionMcpServer,
    saveProjectSkill,
    editProjectSkill,
    toggleProjectSkill,
    deleteProjectSkill,
    saveSecret,
    launchCliLogin,
    testAdapterProfile,
  };
}
