//! the handheld's buttons and sticks from evdev, and what they are to the
//! title under the controls settings.

use std::collections::HashSet;
use std::io::Read;
use std::sync::{Arc, Mutex};

use zakuro_core::services::hid::PadState;

use crate::settings::{Buttons, Settings, Speed, Stick};

pub const A: u16 = 305;
pub const B: u16 = 304;
pub const X: u16 = 307;
pub const Y: u16 = 308;
pub const L1: u16 = 310;
pub const R1: u16 = 311;
pub const L2: u16 = 312;
pub const R2: u16 = 313;
pub const SELECT: u16 = 704;
pub const START: u16 = 705;
pub const R3: u16 = 707;
pub const FN: u16 = 708;
pub const UP: u16 = 544;
pub const DOWN: u16 = 545;
pub const LEFT: u16 = 546;
pub const RIGHT: u16 = 547;
const BUTTONS: [(u16, PadState); 12] = [
    (A, PadState::A),
    (B, PadState::B),
    (X, PadState::X),
    (Y, PadState::Y),
    (L1, PadState::L),
    (R1, PadState::R),
    (SELECT, PadState::SELECT),
    (START, PadState::START),
    (UP, PadState::UP),
    (DOWN, PadState::DOWN),
    (LEFT, PadState::LEFT),
    (RIGHT, PadState::RIGHT),
];
/// a stick's travel, the most it reports.
const STICK: f32 = 1800.0;

#[derive(Default)]
pub struct Pad {
    pub keys: HashSet<u16>,
    pub axes: [i32; 8],
}

#[derive(Default)]
pub struct Touch {
    pub point: Option<(u16, u16)>,
}

pub fn gamepad() -> Option<String> {
    let devices = std::fs::read_to_string("/proc/bus/input/devices").ok()?;
    devices.split("\n\n").find_map(|block| {
        let name = block.lines().find_map(|line| line.strip_prefix("N: Name="))?;
        if !name.contains("Gamepad") && !name.contains("Controller") && !name.contains("Joypad") {
            return None;
        }
        let handlers = block.lines().find_map(|line| line.strip_prefix("H: Handlers="))?;
        let event = handlers.split_whitespace().find(|handler| handler.starts_with("event"))?;
        Some(format!("/dev/input/{event}"))
    })
}

pub fn touchscreen() -> Option<String> {
    let devices = std::fs::read_to_string("/proc/bus/input/devices").ok()?;
    let mut chosen = None;
    for block in devices.split("\n\n") {
        let name = block.lines().find_map(|line| line.strip_prefix("N: Name="))?;
        if name.to_lowercase().contains("touchscreen") {
            let handlers = block.lines().find_map(|line| line.strip_prefix("H: Handlers="))?;
            let event = handlers.split_whitespace().find(|handler| handler.starts_with("event"))?;
            let sysfs = block.lines().find_map(|line| line.strip_prefix("S: Sysfs="))?;
            // Prefer bottom screen on RG DS (i2c-3) if present
            if sysfs.contains("i2c-3") {
                return Some(format!("/dev/input/{event}"));
            }
            if chosen.is_none() {
                chosen = Some(format!("/dev/input/{event}"));
            }
        }
    }
    chosen
}

pub fn read_touch(path: String, touch: Arc<Mutex<Touch>>) {
    std::thread::spawn(move || {
        let Ok(mut device) = std::fs::File::open(&path) else {
            log::error!("could not open touchscreen {path}");
            return;
        };
        let mut event = [0u8; 24];
        let mut cur_x = 0i32;
        let mut cur_y = 0i32;
        let mut down = false;
        while device.read_exact(&mut event).is_ok() {
            let kind = u16::from_ne_bytes([event[16], event[17]]);
            let code = u16::from_ne_bytes([event[18], event[19]]);
            let value = i32::from_ne_bytes([event[20], event[21], event[22], event[23]]);
            match kind {
                1 if code == 330 => { // BTN_TOUCH
                    down = value != 0;
                    if !down {
                        let mut t = touch.lock().unwrap();
                        t.point = None;
                    }
                }
                3 if code == 0x35 || code == 0 => { // ABS_MT_POSITION_X or ABS_X
                    cur_x = value;
                }
                3 if code == 0x36 || code == 1 => { // ABS_MT_POSITION_Y or ABS_Y
                    cur_y = value;
                }
                0 if down => { // EV_SYN
                    // Map 640x480 to 3DS 320x240 (divide by 2)
                    let x_3ds = (cur_x as f32 / 2.0).clamp(0.0, 319.0) as u16;
                    let y_3ds = (cur_y as f32 / 2.0).clamp(0.0, 239.0) as u16;
                    let mut t = touch.lock().unwrap();
                    t.point = Some((x_3ds, y_3ds));
                }
                _ => {}
            }
        }
    });
}

pub fn read_pad(path: String, pad: Arc<Mutex<Pad>>) {
    std::thread::spawn(move || {
        let Ok(mut device) = std::fs::File::open(&path) else {
            log::error!("could not open {path}");
            return;
        };
        let mut event = [0u8; 24];
        while device.read_exact(&mut event).is_ok() {
            let kind = u16::from_ne_bytes([event[16], event[17]]);
            let code = u16::from_ne_bytes([event[18], event[19]]);
            let value = i32::from_ne_bytes([event[20], event[21], event[22], event[23]]);
            let normalized_code = match code {
                314 => SELECT, // Standard Linux Joypad SELECT
                315 => START,  // Standard Linux Joypad START
                316 => FN,     // Standard Linux Joypad BTN_MODE / Home
                318 => R3,     // Standard Linux Joypad R3
                _ => code,
            };
            let mut pad = pad.lock().unwrap();
            match kind {
                1 if value == 0 => {
                    pad.keys.remove(&normalized_code);
                }
                1 => {
                    pad.keys.insert(normalized_code);
                }
                3 if (code as usize) < pad.axes.len() => pad.axes[code as usize] = value,
                _ => {}
            }
        }
    });
}

/// ZAKURO_KEYS=tick:KEY+KEY,... holds the handheld's keys by name from each
/// tick of the frontend's loop, which goes on while the game is paused, for
/// trying the menu without a gamepad.
pub fn parse_keys(spec: &str) -> Vec<(u64, HashSet<u16>)> {
    let code = |name: &str| match name {
        "A" => Some(A),
        "B" => Some(B),
        "X" => Some(X),
        "Y" => Some(Y),
        "L1" => Some(L1),
        "R1" => Some(R1),
        "L2" => Some(L2),
        "R2" => Some(R2),
        "SELECT" => Some(SELECT),
        "START" => Some(START),
        "FN" => Some(FN),
        "UP" => Some(UP),
        "DOWN" => Some(DOWN),
        "LEFT" => Some(LEFT),
        "RIGHT" => Some(RIGHT),
        _ => None,
    };
    spec.split(',')
        .filter_map(|entry| {
            let (tick, keys) = entry.trim().split_once(':')?;
            Some((tick.parse().ok()?, keys.split('+').filter_map(|name| code(name.trim())).collect()))
        })
        .collect()
}

/// a stick's position from -1 to 1, nothing near the middle.
pub fn stick(value: i32) -> f32 {
    if value.abs() < 200 { 0.0 } else { (value as f32 / STICK).clamp(-1.0, 1.0) }
}

/// the title's buttons and circle pad for the keys held and the sticks.
pub fn controls(keys: &HashSet<u16>, axes: &[i32; 8], settings: &Settings) -> (PadState, f32, f32) {
    let mut buttons = PadState::empty();
    for (key, button) in BUTTONS {
        let key = match (settings.buttons, key) {
            (Buttons::Xbox, A) => B,
            (Buttons::Xbox, B) => A,
            (Buttons::Xbox, X) => Y,
            (Buttons::Xbox, Y) => X,
            _ => key,
        };
        if keys.contains(&key) {
            buttons |= button;
        }
    }
    let (x, y) = (stick(axes[0]), -stick(axes[1]));
    match settings.left_stick {
        Stick::CirclePad => (buttons, x, y),
        Stick::Dpad => {
            for (pushed, button) in [(x < -0.5, PadState::LEFT), (x > 0.5, PadState::RIGHT), (y > 0.5, PadState::UP), (y < -0.5, PadState::DOWN)] {
                if pushed {
                    buttons |= button;
                }
            }
            (buttons, 0.0, 0.0)
        }
    }
}

/// how far the touch cursor goes a frame with the right stick held over.
pub fn cursor_step(speed: Speed) -> f32 {
    match speed {
        Speed::Slow => 2.0,
        Speed::Normal => 4.0,
        Speed::Fast => 7.0,
    }
}
