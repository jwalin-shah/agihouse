import {
  CreateStartUpPageContainer,
  RebuildPageContainer,
  TextContainerProperty,
  TextContainerUpgrade,
  type EvenAppBridge,
} from '@evenrealities/even_hub_sdk'

let _bridge: EvenAppBridge | null = null
let _currentLine1 = ''
let _initialized = false

export function setBridge(bridge: EvenAppBridge) {
  _bridge = bridge
}

export async function initHUD(): Promise<boolean> {
  if (!_bridge) return false

  const container = new CreateStartUpPageContainer({
    containerTotalNum: 2,
    textObject: [
      new TextContainerProperty({
        containerID: 1,
        containerName: 'headline',
        xPosition: 10,
        yPosition: 24,
        width: 556,
        height: 72,
        borderWidth: 0,
        paddingLength: 4,
        isEventCapture: 1,
        content: '',
      }),
      new TextContainerProperty({
        containerID: 2,
        containerName: 'detail',
        xPosition: 10,
        yPosition: 110,
        width: 556,
        height: 48,
        borderWidth: 0,
        paddingLength: 4,
        isEventCapture: 0,
        content: '',
      }),
    ],
  })

  const result = await _bridge.createStartUpPageContainer(container)
  _initialized = result === 0
  return _initialized
}

export async function showHUD(line1: string, line2: string): Promise<void> {
  if (!_bridge || !_initialized) return

  // Use textContainerUpgrade for smooth in-place update (no flicker)
  if (_currentLine1 === line1) {
    await _bridge.textContainerUpgrade(
      new TextContainerUpgrade({ containerID: 2, containerName: 'detail', content: line2 }),
    )
  } else {
    await _bridge.rebuildPageContainer(
      new RebuildPageContainer({
        containerTotalNum: 2,
        textObject: [
          new TextContainerProperty({
            containerID: 1,
            containerName: 'headline',
            xPosition: 10,
            yPosition: 24,
            width: 556,
            height: 72,
            borderWidth: 0,
            paddingLength: 4,
            isEventCapture: 1,
            content: line1,
          }),
          new TextContainerProperty({
            containerID: 2,
            containerName: 'detail',
            xPosition: 10,
            yPosition: 110,
            width: 556,
            height: 48,
            borderWidth: 0,
            paddingLength: 4,
            isEventCapture: 0,
            content: line2,
          }),
        ],
      }),
    )
    _currentLine1 = line1
  }
}

export async function clearHUD(): Promise<void> {
  if (!_bridge || !_initialized) return
  await _bridge.rebuildPageContainer(
    new RebuildPageContainer({
      containerTotalNum: 1,
      textObject: [
        new TextContainerProperty({
          containerID: 1,
          containerName: 'headline',
          xPosition: 0,
          yPosition: 0,
          width: 576,
          height: 288,
          isEventCapture: 1,
          content: '',
        }),
      ],
    }),
  )
  _currentLine1 = ''
}
