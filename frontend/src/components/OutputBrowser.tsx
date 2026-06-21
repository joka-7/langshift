import { useEffect, useState } from 'react'
import { getFile, getTree } from '../api'
import type { FileContent, TreeNode } from '../types'

interface Props {
  jobId: string
}

function TreeView({
  node,
  basePath,
  selected,
  onSelect,
}: {
  node: TreeNode
  basePath: string
  selected: string | null
  onSelect: (path: string) => void
}) {
  if (node.type === 'file') {
    return (
      <li>
        <button
          className={basePath === selected ? 'tree-file selected' : 'tree-file'}
          onClick={() => onSelect(basePath)}
        >
          {node.name}
        </button>
      </li>
    )
  }
  return (
    <li>
      <span className="tree-dir">{node.name || '/'}</span>
      <ul>
        {(node.children ?? []).map((child) => (
          <TreeView
            key={child.name}
            node={child}
            basePath={basePath ? `${basePath}/${child.name}` : child.name}
            selected={selected}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </li>
  )
}

export function OutputBrowser({ jobId }: Props) {
  const [tree, setTree] = useState<TreeNode | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<FileContent | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getTree(jobId).then(setTree).catch((e) => setError(e.message))
  }, [jobId])

  useEffect(() => {
    if (!selected) return
    getFile(jobId, selected).then(setFileContent).catch((e) => setError(e.message))
  }, [jobId, selected])

  if (error) return <p className="error">{error}</p>
  if (!tree) return <p>Loading output tree...</p>

  return (
    <div className="output-browser">
      <h3>Output</h3>
      <div className="output-browser-layout">
        <ul className="tree-root">
          <TreeView node={tree} basePath="" selected={selected} onSelect={setSelected} />
        </ul>
        <div className="file-view">
          {fileContent ? (
            <div className="file-view-panes">
              <div>
                <h4>Source</h4>
                <pre>{fileContent.source ?? '(no matching source file found)'}</pre>
              </div>
              <div>
                <h4>Translated</h4>
                <pre>{fileContent.translated}</pre>
              </div>
            </div>
          ) : (
            <p>Select a file to view its contents.</p>
          )}
        </div>
      </div>
    </div>
  )
}
