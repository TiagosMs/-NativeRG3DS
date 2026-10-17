//! where the screens go on a panel, left, top, width and height.

use crate::settings::Layout;

pub fn areas(layout: Layout, swapped: bool, panel_height: i32) -> [Option<[i32; 4]>; 2] {
    if panel_height >= 960 {
        // Dual screen mode (RG DS: top panel 640x480 at Y:0..480, bottom panel 640x480 at Y:480..960)
        let (first, second) = match layout {
            Layout::TopOnly => (Some([0, 48, 640, 384]), None),
            // Top screen: 640x384 (centered in top 640x480 panel)
            // Bottom screen: 640x480 (integer 2x scale on bottom panel at Y:480)
            _ => (Some([0, 48, 640, 384]), Some([0, 480, 640, 480])),
        };
        if swapped {
            let s_swapped = second.map(|[x, y, w, h]| [x, y - 480, w, h]);
            let f_swapped = first.map(|[x, y, w, h]| [x, y + 480, w, h]);
            [s_swapped, f_swapped]
        } else {
            [first, second]
        }
    } else {
        let [first, second] = match layout {
            // the first screen 1.3 times over, the second smaller under it
            Layout::BigTop if swapped => [Some([112, 0, 416, 312]), Some([180, 312, 280, 168])],
            Layout::BigTop => [Some([60, 0, 520, 312]), Some([208, 312, 224, 168])],
            Layout::TopOnly if swapped => [Some([0, 0, 640, 480]), None],
            Layout::TopOnly => [Some([0, 48, 640, 384]), None],
            // both across the panel, eight ninths of their size
            Layout::SideBySide if swapped => [Some([0, 133, 284, 213]), Some([284, 133, 356, 213])],
            Layout::SideBySide => [Some([0, 133, 356, 213]), Some([356, 133, 284, 213])],
            Layout::Original if swapped => [Some([160, 0, 320, 240]), Some([120, 240, 400, 240])],
            Layout::Original => [Some([120, 0, 400, 240]), Some([160, 240, 320, 240])],
        };
        if swapped { [second, first] } else { [first, second] }
    }
}

