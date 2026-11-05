import { createContext, useContext } from 'react'

export interface ConversationTarget {
  id: string
  highlight?: string // the cited excerpt, highlighted in the transcript
}

export const ConversationSheetContext = createContext<(target: ConversationTarget) => void>(
  () => {},
)

/** Open the conversation sheet from anywhere: `useOpenConversation()({ id, highlight })`. */
export const useOpenConversation = () => useContext(ConversationSheetContext)
