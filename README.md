# Introduction
Modify official https://github.com/jellyfin/jellyfin-kodi to suite my own needs
  
Changes:
1. Try to read and parse edl file. Publish edl content as event
2. Try to get outro data from el content, add notification_time in upnext event, so upnext addon can get correct outro info.

# Requirements
## Jellyfin and Kodi not on same device
### Jellyfin settings
Jellyfin library must config correct Shared network folder, which both Jellyfin and Kodi have access to.
### Kodi settings
in FileManager, add the SHared network folder configured in Jellyfin as source
## Jellyfin and Kodi on same device
Add Jellyfin library folder as source in Kodi FileManager

# Usage
## EDL generation
please refer to https://github.com/endrl/jellyfin-plugin-edl
## EDL Notification
When playing starts, event ItemEDLLoaded will be sent out.  
Event data structure:
```
[
  {
    "start": 0.0,
    "end": 87.0,
    "action": 3
  },
  {
    "start": 555.0,
    "end": 980.0,
    "action": 3
  },
  {
    "start": 1111.0,
    "end": null,
    "action": 2
  }
]
```
Please refer to https://kodi.wiki/view/Edit_decision_list for EDL format