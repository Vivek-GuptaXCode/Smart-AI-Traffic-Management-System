'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, LoaderCircle, SendHorizontal, UserRound } from 'lucide-react';
import styles from './TrafficChatbox.module.css';
import { TrafficChatMessage, useTrafficStore } from '@/store/useTrafficStore';

interface ChatInsightsResponse {
  status?: string;
  message?: string;
  response?: string;
  intent?: string;
  confidence?: number;
}

interface TrafficChatboxProps {
  serverUrl: string;
  selectedRsuId?: string | null;
}

const INITIAL_MESSAGE: TrafficChatMessage = {
  id: 'boot-message',
  role: 'assistant',
  content:
    "Traffic assistant is online. Ask about congestion hotspots, RSU-specific status, corridor activity, event-feed summaries, or a system overview.",
  timestamp: new Date().toISOString(),
  intent: 'help',
  confidence: 1,
};

const QUICK_PROMPTS = [
  'Top congested RSUs right now',
  'Summarize event feed in last 30 minutes',
  'Give me an overall traffic summary',
  'Any active green corridor plans?',
  'Status of selected RSU',
];

const parseChatResponse = async (response: Response): Promise<ChatInsightsResponse> => {
  const bodyText = await response.text();
  if (!bodyText.trim()) {
    return {};
  }

  try {
    const parsed = JSON.parse(bodyText);
    if (parsed && typeof parsed === 'object') {
      return parsed as ChatInsightsResponse;
    }
  } catch {
    return { message: bodyText.slice(0, 200) };
  }

  return {};
};

export default function TrafficChatbox({ serverUrl, selectedRsuId }: TrafficChatboxProps) {
  const { chatMessages, appendChatMessage, setChatMessages } = useTrafficStore();
  const [draft, setDraft] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chatMessages.length > 0) {
      return;
    }
    setChatMessages([INITIAL_MESSAGE]);
  }, [chatMessages.length, setChatMessages]);

  const activeRsuLabel = useMemo(() => {
    const normalized = String(selectedRsuId ?? '').trim();
    return normalized || 'Not selected';
  }, [selectedRsuId]);

  useEffect(() => {
    if (!listRef.current) {
      return;
    }
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [chatMessages, isSending]);

  const sendMessage = async (rawMessage: string) => {
    const message = rawMessage.trim();
    if (!message || isSending) {
      return;
    }

    const userMessage: TrafficChatMessage = {
      id: `user_${Date.now()}`,
      role: 'user',
      content: message,
      timestamp: new Date().toISOString(),
    };

    appendChatMessage(userMessage);
    setDraft('');
    setErrorMessage('');
    setIsSending(true);

    try {
      const response = await fetch(`${serverUrl}/chat/insights`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message,
          rsu_id: selectedRsuId ?? undefined,
        }),
      });

      const payload = await parseChatResponse(response);
      if (!response.ok || payload.status !== 'ok') {
        const reason = String(payload.message ?? `Request failed (HTTP ${response.status}).`);
        throw new Error(reason);
      }

      const assistantMessage: TrafficChatMessage = {
        id: `assistant_${Date.now()}`,
        role: 'assistant',
        content: String(payload.response ?? 'No insight payload was returned.'),
        timestamp: new Date().toISOString(),
        intent: payload.intent,
        confidence: typeof payload.confidence === 'number' ? payload.confidence : undefined,
      };

      appendChatMessage(assistantMessage);
    } catch (error) {
      const readableMessage =
        error instanceof Error ? error.message : 'Unable to connect to backend chat endpoint.';
      setErrorMessage(readableMessage);

      const fallbackMessage: TrafficChatMessage = {
        id: `system_${Date.now()}`,
        role: 'system',
        content: `Chat request failed: ${readableMessage}`,
        timestamp: new Date().toISOString(),
      };
      appendChatMessage(fallbackMessage);
    } finally {
      setIsSending(false);
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await sendMessage(draft);
  };

  return (
    <div className={styles.chatShell}>
      <div className={styles.chatHeader}>
        <div>
          <p className={styles.headerTitle}>Mission Chat Console</p>
          <p className={styles.headerSubtitle}>Ask for RSU-specific traffic intelligence in natural language.</p>
        </div>
        <div className={styles.contextBadge}>RSU Context: {activeRsuLabel}</div>
      </div>

      <div className={styles.quickPromptRow}>
        {QUICK_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className={styles.quickPromptButton}
            disabled={isSending}
            onClick={() => {
              const resolvedPrompt =
                prompt === 'Status of selected RSU' && activeRsuLabel !== 'Not selected'
                  ? `Status summary for RSU ${activeRsuLabel}`
                  : prompt;
              void sendMessage(resolvedPrompt);
            }}
          >
            {prompt}
          </button>
        ))}
      </div>

      <div className={styles.messages} ref={listRef}>
        {chatMessages.map((message) => {
          const timestamp = new Date(message.timestamp).toLocaleTimeString();
          return (
            <article
              key={message.id}
              className={`${styles.messageCard} ${
                message.role === 'user'
                  ? styles.userMessage
                  : message.role === 'system'
                    ? styles.systemMessage
                    : styles.assistantMessage
              }`}
            >
              <div className={styles.messageMeta}>
                <span className={styles.roleBadge}>
                  {message.role === 'user' ? <UserRound size={14} /> : <Bot size={14} />}
                  {message.role.toUpperCase()}
                </span>
                <span className={styles.timestampText}>{timestamp}</span>
              </div>

              <p className={styles.messageText}>{message.content}</p>

              {(message.intent || typeof message.confidence === 'number') && (
                <div className={styles.messageTelemetry}>
                  {message.intent ? <span>intent: {message.intent}</span> : null}
                  {typeof message.confidence === 'number' ? (
                    <span>confidence: {(message.confidence * 100).toFixed(0)}%</span>
                  ) : null}
                </div>
              )}
            </article>
          );
        })}

        {isSending ? (
          <article className={`${styles.messageCard} ${styles.assistantMessage}`}>
            <div className={styles.typingIndicator}>
              <LoaderCircle size={16} className={styles.spinning} />
              <span>Analyzing live RSU data...</span>
            </div>
          </article>
        ) : null}
      </div>

      <form className={styles.composer} onSubmit={handleSubmit}>
        <input
          type="text"
          placeholder="Ask about RSU performance, hotspots, or corridors"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={isSending}
          className={styles.input}
        />
        <button
          type="submit"
          className={styles.sendButton}
          disabled={isSending || !draft.trim()}
          aria-label="Send message"
        >
          <SendHorizontal size={16} />
          Send
        </button>
      </form>

      {errorMessage ? <p className={styles.errorText}>{errorMessage}</p> : null}
    </div>
  );
}