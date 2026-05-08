$fn = 32;

// ==========================================================
// PARAMETRAR — JUSTERA HÄR
// ==========================================================

// Yttre referensmått: centrum på dina ursprungliga yttersta små hål
outer_w = 170.5;
outer_h = 109.75;

// Global placering, som i din gamla modell
origin = [5, 5, 0];

// Bottenplatta, centrerad i outer footprint
plate_w = 152;
plate_h = 94;
plate_t = 1.6;

plate_x = (outer_w - plate_w) / 2;
plate_y = (outer_h - plate_h) / 2;

// Svävande höjd för vägg/låda
floating_z = 4;

// Vägg
wall_t = 2;
wall_z_h = 18;

// Hörnpelare / ytterhål
corner_pillar_r = 1.5;   // motsvarar cylinder(h=5, r=1.5)
corner_hole_r   = 0.5;   // små hörnhål, diameter 1.0 mm

// Väggen får exakt gå till hull av hörnpelarna
wall_outer_r = corner_pillar_r;

// Stödarmar
support_small_d = 2 * corner_pillar_r;
support_big_d   = 7.5;


// ==========================================================
// TESTADE SKRUVHÅL FÖR DIN SKRIVARE/SKRUVAR
// ==========================================================

m2_hole_d   = 2.2;
m25_hole_d  = 2.6;
m3_hole_d   = 3.1;


// ==========================================================
// USB-A CUTOUT FÖR RPI5
// ==========================================================

// Nedre väggsidan, flyttad åt höger
usb_a_cutout_x = 30;
usb_a_cutout_w = 40;

usb_a_cutout_y = -wall_outer_r - 1;
usb_a_cutout_z = floating_z - 1;
usb_a_cutout_h = wall_z_h + 4;

// Extra stöd på varsin sida om USB-A-cutouten
usb_side_beam_d = 5;
usb_side_beam_h = 5;
usb_side_beam_gap = 3;
usb_side_beam_len = 13;


// ==========================================================
// USB-C CUTOUT FÖR RPI5, VÄNSTER VÄGG X-
// ==========================================================

// Center 89 mm från nederkant av lådan
usb_c_cutout_center_y = 89;

// USB-C-uttag i vänster vägg.
// w = storlek längs Y, h = storlek i Z.
// Förstorad för stora gjutna USB-C-kontakter.
usb_c_cutout_w = 18;
usb_c_cutout_h = 9;

// Placera ungefär i nivå med RPi5-kortets USB-C-port.
// RPi-kortets undersida ligger vid plate_t + pi_standoff_h = 1.6 + 9 = 10.6
usb_c_cutout_center_z = plate_t + 9 + 3.5;

usb_c_cutout_x = -wall_outer_r - 1;
usb_c_cutout_y = usb_c_cutout_center_y - usb_c_cutout_w / 2;
usb_c_cutout_z = usb_c_cutout_center_z - usb_c_cutout_h / 2;
usb_c_cutout_depth = wall_outer_r + wall_t + 4;

// Stöd runt USB-C-uttaget
usb_c_side_beam_d = 5;
usb_c_side_beam_h = 5;
usb_c_side_beam_gap = 3;
usb_c_side_beam_len = 13;


// ==========================================================
// TVÅ NOTCHAR I UNDERKANT AV ÖVRE VÄGGEN, Y+
// ==========================================================

// Två små notchar i väggen på övre kanten, mot y+.
// Center från vänster kant av lådan: 66 och 102 mm.
top_notch_w = 4;
top_notch_h = 4;
top_notch_centers_x = [66, 102];

// Skär genom övre väggens tjocklek
top_notch_y = outer_h - wall_t - 2;
top_notch_depth_y = wall_t + wall_outer_r + 4;


// ==========================================================
// LOCKFÄSTEN — M3 DIREKT I PLAST
// ==========================================================

lid_screw_pilot_d = m3_hole_d;

lid_tower_d = 8.0;
lid_tower_h = wall_z_h;
lid_tower_inset = 7.0;


// ==========================================================
// RASPBERRY PI 5 — M2.5 DIREKT I PLAST
// ==========================================================

// Pi 5-kort: 85 x 56 mm
// Monteringshål: M2.5-ish, antagen hålbild 58 x 49 mm,
// offset 3.5 mm från hörn.
pi_size = [85, 56];

pi_holes = [
    [3.5, 3.5],
    [61.5, 3.5],
    [3.5, 52.5],
    [61.5, 52.5]
];

// Pi i porträttläge, roterad 180° mot tidigare pi_rot=90
pi_rot = 270;
pi_pos = [12, 97.5, 0];

// Pi-pelare direkt från bottenplattan
pi_standoff_base_z = plate_t;
pi_standoff_h = 9;
pi_standoff_d = 7.2;

// M2.5-skruv direkt i plast
pi_screw_pilot_d = m25_hole_d;


// ==========================================================
// JC3248S035 — 3.5" SPI DISPLAY
// ==========================================================

display_size = [99.0, 54.9];

display_rot = 90;
display_pos = [158, 5.5, 0];

// Låst D5 från pappersmall:
// X-offset från vänster/höger kortkant = 2.75 mm
// Y-offset från under/över kortkant = 4.25 mm
display_hole_offset = [2.75, 4.25];

display_holes = [
    [display_hole_offset[0], display_hole_offset[1]],
    [display_size[0] - display_hole_offset[0], display_hole_offset[1]],
    [display_size[0] - display_hole_offset[0], display_size[1] - display_hole_offset[1]],
    [display_hole_offset[0], display_size[1] - display_hole_offset[1]]
];

// Display upp över locket
display_standoff_base_z = plate_t;

display_raise_above_lid = 2;
display_board_z = floating_z + wall_z_h + display_raise_above_lid;
display_standoff_h = display_board_z - display_standoff_base_z;

display_standoff_d = 8.0;

// M3 direkt i plast
display_screw_pilot_d = m3_hole_d;


// ==========================================================
// KAMERA — M2 DIREKT I PLAST, LUTAD 15°
// ==========================================================

camera_size = [25, 24];

// Kamera centrerad
camera_pos_x = outer_w / 2;
camera_y_margin = 10;

camera_pos = [
    camera_pos_x - camera_size[0] / 2,
    outer_h + wall_outer_r - wall_t - camera_size[1] - camera_y_margin,
    0
];

// Låst K2 från pappersmall:
// Kamerahål CC = 20.75 mm i bredd, 13.50 mm i höjd
camera_hole_cc = [20.75, 13.50];

camera_hole_offset = [
    (camera_size[0] - camera_hole_cc[0]) / 2,
    (camera_size[1] - camera_hole_cc[1]) / 2
];

camera_holes = [
    [camera_hole_offset[0], camera_hole_offset[1]],
    [camera_hole_offset[0] + camera_hole_cc[0], camera_hole_offset[1]],
    [camera_hole_offset[0], camera_hole_offset[1] + camera_hole_cc[1]],
    [camera_hole_offset[0] + camera_hole_cc[0], camera_hole_offset[1] + camera_hole_cc[1]]
];

// Kamera upp över locket
camera_standoff_base_z = plate_t;

camera_raise_above_lid = 2;
camera_board_z = floating_z + wall_z_h + camera_raise_above_lid;

// Positiv X-rotation lutar kamerans normal mot y-.
// Om den lutar åt fel håll: byt till -15.
camera_tilt_deg = 15;

// Liten frigång så toppen kapas strax ovanför kortplanet.
// Sätt 0 om du vill att pelartoppen exakt slutar i kamerans plan.
camera_top_clearance = 0.15;

camera_standoff_d = 6.5;

// M2 direkt i plast
camera_screw_pilot_d = m2_hole_d;


// ==========================================================
// CUTOUTS
// ==========================================================

// Antenn-cutout är borttagen helt.

// Kabelhål.
// Positionen behåller gamla vänster-/nederkant.
// Storleken är +2 mm åt x+ och +2 mm åt y+.
// Detta skärs globalt genom hela modellen.
cable_cutout_size = [44, 12, 30];
cable_cutout_pos = [
    outer_w / 2 - 42 / 2,
    119 - 64 - 10 + 1 - 5,
    0
];

// Top cutout skär bara bottenplattan
top_cutout_size = [12, 12, 30];
top_cutout_pos = [
    outer_w / 2 - top_cutout_size[0] / 2,
    119 - 12 - 4 - 5,
    0
];


// ==========================================================
// DEBUG
// ==========================================================

show_pi_footprint = true;
show_display_footprint = true;
show_camera_footprint = true;
show_keepouts = true;
show_wall_footprint = false;


// ==========================================================
// HJÄLPARE
// ==========================================================

function corner_centers() = [
    [0, 0],
    [outer_w, 0],
    [outer_w, outer_h],
    [0, outer_h]
];

module wall_outer_2d() {
    hull() {
        for (p = corner_centers()) {
            translate(p)
                circle(r = wall_outer_r);
        }
    }
}

module wall_inner_2d() {
    offset(delta = -wall_t)
        wall_outer_2d();
}

// Klipp geometri så den aldrig sticker utanför ytterväggens hull.
// Används för beams/stöd som annars kan sticka ut p.g.a. cylinderdiametrar.
module keep_inside_outer_hull(zmin=-20, zmax=80) {
    intersection() {
        children();

        translate([0, 0, zmin])
            linear_extrude(height = zmax - zmin)
                wall_outer_2d();
    }
}

module beam(a, b, h=2.5, d=6) {
    hull() {
        translate(a)
            cylinder(h=h, d=d);

        translate(b)
            cylinder(h=h, d=d);
    }
}

module myline(from, from_h, from_d, to, to_h, to_d) {
    hull() {
        translate(from)
            cylinder(h = from_h, d = from_d);

        translate(to)
            cylinder(h = to_h, d = to_d);
    }
}

module simple_standoff(h=5, d=5.5, screw_d=2.2) {
    difference() {
        union() {
            cylinder(h=h, d=d);
            cylinder(h=1.0, d=d+2);
        }

        translate([0, 0, -0.5])
            cylinder(h=h+1, d=screw_d);
    }
}

module lid_tower() {
    difference() {
        cylinder(h=lid_tower_h, d=lid_tower_d);

        translate([0, 0, -0.5])
            cylinder(h=lid_tower_h+1, d=lid_screw_pilot_d);
    }
}


// ==========================================================
// KAMERA-TILT HJÄLPARE
// ==========================================================

function camera_center() = [
    camera_pos[0] + camera_size[0] / 2,
    camera_pos[1] + camera_size[1] / 2,
    camera_board_z
];

// Global position för ett hål på det tiltade kamerakortet
function camera_hole_top(p) =
    let (
        cx = camera_size[0] / 2,
        cy = camera_size[1] / 2,
        dx = p[0] - cx,
        dy = p[1] - cy
    )
    [
        camera_center()[0] + dx,
        camera_center()[1] + dy * cos(camera_tilt_deg),
        camera_center()[2] + dy * sin(camera_tilt_deg)
    ];

// Basposition för pelaren.
// Den flyttas lite i Y så att den hamnar under den sneda skruvlinjen.
// Botten är dock fortfarande en plan cylinder mot bottenplattan.
function camera_hole_bottom(p) =
    let (
        top = camera_hole_top(p),
        run = (top[2] - camera_standoff_base_z) / cos(camera_tilt_deg)
    )
    [
        top[0],
        top[1] + sin(camera_tilt_deg) * run,
        camera_standoff_base_z
    ];

module camera_board_transform() {
    translate(camera_center())
        rotate([camera_tilt_deg, 0, 0])
            translate([-camera_size[0]/2, -camera_size[1]/2, 0])
                children();
}

module tilted_camera_screw_hole(p, extra=10) {
    top = camera_hole_top(p);

    // Cylindern ligger längs kamerakortets normal.
    // Den börjar en bit under kortet och går igenom hela pelaren.
    translate([
        top[0],
        top[1] + sin(camera_tilt_deg) * extra,
        top[2] - cos(camera_tilt_deg) * extra
    ])
        rotate([camera_tilt_deg, 0, 0])
            cylinder(h=80, d=camera_screw_pilot_d);
}

module tilted_camera_standoff(p) {
    top = camera_hole_top(p);
    bot = camera_hole_bottom(p);

    overshoot = 8;

    difference() {
        hull() {
            // Botten: plan cylinder/fot mot lådans botten
            translate(bot)
                cylinder(h=1.2, d=camera_standoff_d + 2);

            // Toppen: extra hög vertikal massa som kapas i lutande plan
            translate([top[0], top[1], top[2] - 1.0])
                cylinder(h=overshoot, d=camera_standoff_d);
        }

        // Lutande topplan, samma vinkel som kamerakortet.
        // Allt ovanför detta plan skärs bort.
        camera_board_transform()
            translate([
                -camera_size[0],
                -camera_size[1],
                camera_top_clearance
            ])
                cube([
                    camera_size[0] * 3,
                    camera_size[1] * 3,
                    overshoot * 4
                ]);

        // Sned M2-skruvkanal, vinkelrät mot kamerakortet
        tilted_camera_screw_hole(p);
    }
}


// ==========================================================
// CUTOUT-MODULER
// ==========================================================

module usb_a_cutout_volume() {
    translate([
        usb_a_cutout_x,
        usb_a_cutout_y,
        usb_a_cutout_z
    ])
        cube([
            usb_a_cutout_w,
            wall_outer_r + wall_t + 4,
            usb_a_cutout_h
        ]);
}

module usb_c_cutout_volume() {
    translate([
        usb_c_cutout_x,
        usb_c_cutout_y,
        usb_c_cutout_z
    ])
        cube([
            usb_c_cutout_depth,
            usb_c_cutout_w,
            usb_c_cutout_h
        ]);
}

module cable_cutout_volume() {
    translate(cable_cutout_pos)
        cube(cable_cutout_size);
}

module top_wall_notch_volume(cx) {
    translate([
        cx - top_notch_w/2,
        top_notch_y,
        floating_z - 0.1
    ])
        cube([
            top_notch_w,
            top_notch_depth_y,
            top_notch_h + 0.2
        ]);
}

module top_wall_notches_volume() {
    for (cx = top_notch_centers_x)
        top_wall_notch_volume(cx);
}


// ==========================================================
// BOTTENPLATTA
// ==========================================================

module base_plate() {
    difference() {
        translate([plate_x, plate_y, 0])
            cube([plate_w, plate_h, plate_t]);

        // Top cutout är fortfarande lokal till bottenplattan
        translate(top_cutout_pos)
            cube(top_cutout_size);

        // Kabelhålet skärs globalt i model_local()
    }
}


// ==========================================================
// SVÄVANDE VÄGG / LÅDA
// ==========================================================

module floating_wall_frame() {
    difference() {
        // Ytterkontur: exakt hull av cylinder(h=5, r=1.5)
        // vid de yttersta små hålen/pelarna.
        translate([0, 0, floating_z])
            linear_extrude(height = wall_z_h)
                wall_outer_2d();

        // Innerkontur: offsetad inåt med wall_t.
        translate([0, 0, floating_z - 0.5])
            linear_extrude(height = wall_z_h + 1)
                wall_inner_2d();

        usb_a_cutout_volume();
        usb_c_cutout_volume();
        top_wall_notches_volume();
    }
}


// ==========================================================
// STÖD TILL SVÄVANDE STRUKTUR
// ==========================================================

module floating_outer_supports_raw() {
    support_h = 5;

    x0 = 0;
    x1 = outer_w;
    y0 = 0;
    y1 = outer_h;

    xi = 12;
    yi = 10;

    // Fyra diagonala hörnstöd
    myline(
        [x0, y0, floating_z+1], support_h, support_small_d,
        [x0 + xi, y0 + yi, 0], support_h, support_big_d
    );

    myline(
        [x1, y0, floating_z+1], support_h, support_small_d,
        [x1 - xi, y0 + yi, 0], support_h, support_big_d
    );

    myline(
        [x1, y1, floating_z+1], support_h, support_small_d,
        [x1 - xi, y1 - yi, 0], support_h, support_big_d
    );

    myline(
        [x0, y1, floating_z+1], support_h, support_small_d,
        [x0 + xi, y1 - yi, 0], support_h, support_big_d
    );

    // Extra stöd på övre långsidan
    beam(
        [outer_w*0.30, y1, floating_z],
        [outer_w*0.30, y1 - 10, 0],
        h=support_h,
        d=5
    );

    beam(
        [outer_w*0.70, y1, floating_z],
        [outer_w*0.70, y1 - 10, 0],
        h=support_h,
        d=5
    );

    // Nedre långsida: stöd höger om USB-A-cutouten
    beam(
        [outer_w*0.70, y0, floating_z],
        [outer_w*0.70, y0 + 10, 0],
        h=support_h,
        d=5
    );
}

module floating_outer_supports() {
    keep_inside_outer_hull()
        floating_outer_supports_raw();
}


// ==========================================================
// USB-A-CUTOUT SIDOSTÖD
// ==========================================================

module usb_a_cutout_side_beams_raw() {
    x_left  = usb_a_cutout_x - usb_side_beam_gap;
    x_right = usb_a_cutout_x + usb_a_cutout_w + usb_side_beam_gap;

    beam(
        [x_left, 0, floating_z],
        [x_left, usb_side_beam_len, 0],
        h = usb_side_beam_h,
        d = usb_side_beam_d
    );

    beam(
        [x_right, 0, floating_z],
        [x_right, usb_side_beam_len, 0],
        h = usb_side_beam_h,
        d = usb_side_beam_d
    );
}

module usb_a_cutout_side_beams() {
    keep_inside_outer_hull()
        usb_a_cutout_side_beams_raw();
}


// ==========================================================
// USB-C-CUTOUT SIDOSTÖD
// ==========================================================

module usb_c_cutout_side_beams_raw() {
    y_low  = usb_c_cutout_center_y - usb_c_cutout_w/2 - usb_c_side_beam_gap;
    y_high = usb_c_cutout_center_y + usb_c_cutout_w/2 + usb_c_side_beam_gap;

    beam(
        [0, y_low, floating_z],
        [usb_c_side_beam_len, y_low, 0],
        h = usb_c_side_beam_h,
        d = usb_c_side_beam_d
    );

    beam(
        [0, y_high, floating_z],
        [usb_c_side_beam_len, y_high, 0],
        h = usb_c_side_beam_h,
        d = usb_c_side_beam_d
    );
}

module usb_c_cutout_side_beams() {
    keep_inside_outer_hull()
        usb_c_cutout_side_beams_raw();
}


// ==========================================================
// LOCKFÄSTEN
// ==========================================================

module lid_mounts() {
    z = floating_z;

    pts = [
        [lid_tower_inset, lid_tower_inset],
        [outer_w - lid_tower_inset, lid_tower_inset],
        [outer_w - lid_tower_inset, outer_h - lid_tower_inset],
        [lid_tower_inset, outer_h - lid_tower_inset]
    ];

    for (p = pts) {
        translate([p[0], p[1], z])
            lid_tower();
    }
}


// ==========================================================
// HÖRNANKARE / HÅL
// ==========================================================

module corner_anchor_struts_raw() {
    translate([0, 0, 4]) {
        myline([0, 0, 1], 5, 3, [12, 10, -4], 5, 7);
        cylinder(h=3, r=corner_pillar_r);
    }

    translate([outer_w, 0, 4]) {
        myline([0, 0, 1], 5, 3, [-12, 10, -4], 5, 7);
        cylinder(h=5, r=corner_pillar_r);
    }

    translate([outer_w, outer_h, 4]) {
        myline([0, 0, 1], 5, 3, [-12, -10, -4], 5, 7);
        cylinder(h=5, r=corner_pillar_r);
    }

    translate([0, outer_h, 4]) {
        myline([0, 0, 1], 5, 3, [12, -10, -4], 5, 7);
        cylinder(h=5, r=corner_pillar_r);
    }
}

module corner_anchor_struts() {
    keep_inside_outer_hull()
        corner_anchor_struts_raw();
}

module corner_anchor_holes() {
    for (p = corner_centers()) {
        translate([p[0], p[1], 4])
            cylinder(h=20, r=corner_hole_r);
    }
}


// ==========================================================
// RASPBERRY PI 5
// ==========================================================

module raspberry_pi_mount() {
    translate([pi_pos[0], pi_pos[1], 0])
    rotate([0, 0, pi_rot]) {
        for (p = pi_holes) {
            translate([p[0], p[1], pi_standoff_base_z])
                simple_standoff(
                    h = pi_standoff_h,
                    d = pi_standoff_d,
                    screw_d = pi_screw_pilot_d
                );
        }

        if (show_pi_footprint) {
            %translate([0, 0, pi_standoff_base_z + pi_standoff_h])
                cube([pi_size[0], pi_size[1], 1.2]);

            for (p = pi_holes) {
                %translate([p[0], p[1], pi_standoff_base_z + pi_standoff_h])
                    cylinder(h=3, d=3);
            }
        }
    }
}


// ==========================================================
// DISPLAY
// ==========================================================

module display_mount() {
    translate([display_pos[0], display_pos[1], 0])
    rotate([0, 0, display_rot]) {
        for (p = display_holes) {
            translate([p[0], p[1], display_standoff_base_z])
                simple_standoff(
                    h=display_standoff_h,
                    d=display_standoff_d,
                    screw_d=display_screw_pilot_d
                );
        }

        beam(
            [display_holes[0][0], display_holes[0][1], display_standoff_base_z],
            [display_holes[1][0], display_holes[1][1], display_standoff_base_z],
            h=2,
            d=5
        );

        beam(
            [display_holes[1][0], display_holes[1][1], display_standoff_base_z],
            [display_holes[2][0], display_holes[2][1], display_standoff_base_z],
            h=2,
            d=5
        );

        beam(
            [display_holes[2][0], display_holes[2][1], display_standoff_base_z],
            [display_holes[3][0], display_holes[3][1], display_standoff_base_z],
            h=2,
            d=5
        );

        beam(
            [display_holes[3][0], display_holes[3][1], display_standoff_base_z],
            [display_holes[0][0], display_holes[0][1], display_standoff_base_z],
            h=2,
            d=5
        );

        if (show_display_footprint) {
            %translate([0, 0, display_standoff_base_z + display_standoff_h])
                cube([display_size[0], display_size[1], 1.2]);

            for (p = display_holes) {
                %translate([p[0], p[1], display_standoff_base_z + display_standoff_h])
                    cylinder(h=3, d=3);
            }
        }
    }
}


// ==========================================================
// KAMERA — LUTAD
// ==========================================================

module camera_mount() {
    // Tiltade pelare med sneda M2-hål och vinklade toppar
    for (p = camera_holes) {
        tilted_camera_standoff(p);
    }

    // Basförstärkning mellan pelarnas bottenpunkter.
    // Dessa ligger plant mot bottennivån.
    beam(
        camera_hole_bottom(camera_holes[0]),
        camera_hole_bottom(camera_holes[1]),
        h=2,
        d=4.5
    );

    beam(
        camera_hole_bottom(camera_holes[2]),
        camera_hole_bottom(camera_holes[3]),
        h=2,
        d=4.5
    );

    beam(
        camera_hole_bottom(camera_holes[0]),
        camera_hole_bottom(camera_holes[2]),
        h=2,
        d=4.5
    );

    beam(
        camera_hole_bottom(camera_holes[1]),
        camera_hole_bottom(camera_holes[3]),
        h=2,
        d=4.5
    );

    if (show_camera_footprint) {
        %camera_board_transform()
            cube([camera_size[0], camera_size[1], 1.0]);

        for (p = camera_holes) {
            top = camera_hole_top(p);

            %translate(top)
                rotate([camera_tilt_deg, 0, 0])
                    cylinder(h=3, d=3);
        }
    }
}


// ==========================================================
// DEBUG
// ==========================================================

module debug_keepouts() {
    if (show_keepouts) {
        %cable_cutout_volume();
        %usb_a_cutout_volume();
        %usb_c_cutout_volume();

        for (cx = top_notch_centers_x)
            %top_wall_notch_volume(cx);

        %translate(top_cutout_pos)
            cube([top_cutout_size[0], top_cutout_size[1], 2]);
    }

    if (show_wall_footprint) {
        %translate([0, 0, floating_z])
            linear_extrude(height = 1)
                wall_outer_2d();
    }
}


// ==========================================================
// SLUTMODELL
// ==========================================================

module model_local() {
    difference() {
        union() {
            base_plate();

            // Svävande vägg/låda
            floating_wall_frame();

            // Stöd till svävande struktur
            floating_outer_supports();

            // Stöd runt USB-A-uttaget
            usb_a_cutout_side_beams();

            // Stöd runt USB-C-uttaget
            usb_c_cutout_side_beams();

            // Locktorn
            lid_mounts();

            // Hörnankare
            corner_anchor_struts();

            // Direktmonterade komponentpelare
            raspberry_pi_mount();
            display_mount();
            camera_mount();

            debug_keepouts();
        }

        // Hörnhål
        corner_anchor_holes();

        // Vägguttag / genomföringar
        usb_a_cutout_volume();
        usb_c_cutout_volume();

        // Notchar i övre väggens underkant
        top_wall_notches_volume();

        // Kabelhål genom all geometri
        cable_cutout_volume();

        // Top cutout skär bara base_plate()
    }
}

translate(origin)
    model_local();