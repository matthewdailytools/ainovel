export function SyncScopePreview({ title }: { title?: string }) {
  return (
    <section aria-label="本次作品同步范围">
      <p className="small">
        {title ? `同步《${title}》的当前工作版本。` : '同步当前作品的工作版本。'}
        包含目录、正式章稿与历史、已确认立项与设定、手法卡和出处、大纲、故事记录、已确认的设定使用证据、历史体检与交付。
      </p>
      <p className="small">
        原始资料和附件按所选文件上传。未确认草稿、手法卡原句、模型密钥、费用和运行日志留在本机。
        设定使用证据只传版本、原文位置和校验值，按对应章稿回读原文。设备凭据用于当前页面的连接。
      </p>
    </section>
  )
}
