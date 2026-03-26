import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { DashboardData } from './types/dashboard';
import type { ChatMessage, TeamChatProgress, LastChatRun, StoredChatConfig } from './types/chat';

interface IdeState {
    // Global Session
    workspaceId: string;
    setWorkspaceId: (id: string) => void;

    // File Explorer & Editor
    selectedFile: string | null;
    setSelectedFile: (file: string | null) => void;

    activeDiff: { original: string; modified: string; path: string } | null;
    setActiveDiff: (diff: { original: string; modified: string; path: string } | null) => void;

    // Terminal
    isTerminalOpen: boolean;
    setTerminalOpen: (isOpen: boolean) => void;

    // FinOps & Telemetry (Phase 5.3)
    dashboardData: DashboardData | null;
    setDashboardData: (data: DashboardData | null) => void;

    // Chat & Handoff (Phase 5.4)
    chatMessages: ChatMessage[];
    addChatMessage: (msg: ChatMessage) => void;
    clearChatMessages: () => void;

    chatProgress: TeamChatProgress | null;
    setChatProgress: (progress: TeamChatProgress | null) => void;

    lastChatRun: LastChatRun | null;
    setLastChatRun: (run: LastChatRun | null) => void;

    chatConfig: StoredChatConfig;
    setChatConfig: (config: Partial<StoredChatConfig>) => void;
}

export const useIdeStore = create<IdeState>()(
    persist(
        (set) => ({
            workspaceId: 'default',
            setWorkspaceId: (id) => set({ workspaceId: id }),

            selectedFile: null,
            setSelectedFile: (file) => set({ selectedFile: file, activeDiff: null }),

            activeDiff: null,
            setActiveDiff: (diff) => set({ activeDiff: diff, selectedFile: null }),

            isTerminalOpen: true,
            setTerminalOpen: (isOpen) => set({ isTerminalOpen: isOpen }),

            dashboardData: null,
            setDashboardData: (data) => set({ dashboardData: data }),

            chatMessages: [],
            addChatMessage: (msg) => set((state) => ({ chatMessages: [...state.chatMessages, msg] })),
            clearChatMessages: () => set({ chatMessages: [] }),

            chatProgress: null,
            setChatProgress: (progress) => set({ chatProgress: progress }),

            lastChatRun: null,
            setLastChatRun: (run) => set({ lastChatRun: run }),

            chatConfig: {
                mode: 'sprint5',
                rounds: 3,
                complexity: 'medium',
                criticality: 'medium',
                strictMode: true,
                allowLowProductivityOverride: false,
            },
            setChatConfig: (config) =>
                set((state) => ({ chatConfig: { ...state.chatConfig, ...config } })),
        }),
        {
            name: 'aiteam-ide-storage',
            partialize: (state) => ({
                workspaceId: state.workspaceId,
                isTerminalOpen: state.isTerminalOpen,
                chatConfig: state.chatConfig,
            }), // Only persist specific UI preferences
        }
    )
);
