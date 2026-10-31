; ============================================================================
; SW_Guided_Reader 安装器钩子
;
; 设计原则：
;   · 卸载时只清理程序自身目录（reader-service、dailyread-skill、uninstall.exe）
;   · 安装目录下的 Reader 文件夹是用户数据（教材、笔记、学习记录），必须保留
;   · 安装目录仅在为空时删除，因此 Reader 存在时目录会保留下来
; ============================================================================

!macro NSIS_HOOK_POSTUNINSTALL
  ; 后端与技能包再兜底清理一次：安装时按文件清单逐个删除，
  ; 遇到被杀毒/索引服务短暂占用的文件会失败，这里整目录重试。
  RMDir /r "$INSTDIR\reader-service"
  RMDir /r "$INSTDIR\dailyread-skill"

  ; 卸载器自身在运行中无法立即删除，这里补一刀（失败则由系统重启后清理）
  Delete "$INSTDIR\uninstall.exe"

  ; 仅当目录已空（即没有 Reader 数据）时才移除安装目录
  RMDir "$INSTDIR"
!macroend
