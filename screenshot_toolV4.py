import tkinter as tk
from tkinter import filedialog
import os
import json
import time
from PIL import ImageGrab, ImageTk
from datetime import datetime
import screeninfo

class ScreenshotTool:
    SETTINGS_FILE = "screenshot_settings.json"
    
    def __init__(self, root):
        self.root = root
        self.root.title("Screenshot Tool")
        
        # Default settings
        self.settings = {
            "master_folder": "",
            "window_geometry": None,
            "window_state": {}
        }
        self.selected_area = None
        self.last_screenshot = None
        
        # Notification variables
        self.notification = None
        self.notification_id = None
        
        # Load settings
        self.load_settings()
        
        # UI state storage
        self.ui_state = {
            "folder_name": "",
            "coordinates": [0, 0, 0, 0]
        }
        
        # Selection variables
        self.selection_active = False
        self.crosshair_lines = []
        self.coord_label = None
        self.virtual_screen = self.get_virtual_screen()
        
        # Main GUI elements
        self.create_main_gui()
        
    def get_virtual_screen(self):
        """Get the combined dimensions of all monitors"""
        try:
            monitors = screeninfo.get_monitors()
            if not monitors:
                width, height = ImageGrab.grab().size
                return (0, 0, width, height)
            
            min_x = min(m.x for m in monitors)
            min_y = min(m.y for m in monitors)
            max_x = max(m.x + m.width for m in monitors)
            max_y = max(m.y + m.height for m in monitors)
            return (min_x, min_y, max_x - min_x, max_y - min_y)
        except Exception as e:
            print(f"Error getting monitor info: {e}")
            width, height = ImageGrab.grab().size
            return (0, 0, width, height)

    def load_settings(self):
        """Load settings from JSON file"""
        try:
            if os.path.exists(self.SETTINGS_FILE):
                with open(self.SETTINGS_FILE, 'r') as f:
                    loaded_settings = json.load(f)
                    self.settings.update(loaded_settings)
        except Exception as e:
            print(f"Error loading settings: {e}")

    def save_settings(self):
        """Save settings to JSON file"""
        try:
            with open(self.SETTINGS_FILE, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def save_ui_state(self):
        """Save current UI state"""
        if hasattr(self, 'folder_entry'):
            self.ui_state["folder_name"] = self.folder_entry.get()
        if hasattr(self, 'coord_vars') and self.coord_vars:
            self.ui_state["coordinates"] = [var.get() for var in self.coord_vars]
        
        self.settings["window_geometry"] = self.root.geometry()
        self.save_settings()

    def restore_ui_state(self):
        """Restore UI state"""
        if hasattr(self, 'folder_entry'):
            self.folder_entry.delete(0, tk.END)
            self.folder_entry.insert(0, self.ui_state["folder_name"])
        if hasattr(self, 'coord_vars') and self.coord_vars and self.selected_area:
            for i, var in enumerate(self.coord_vars):
                var.set(str(self.ui_state["coordinates"][i]))

    def create_main_gui(self):
        """Create the main application interface"""
        for widget in self.root.winfo_children():
            widget.destroy()
        
        # Main container frame
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(expand=True, fill=tk.BOTH)
        
        # Folder name entry
        tk.Label(main_frame, text="Folder Name:").pack(pady=(10, 0))
        self.folder_entry = tk.Entry(main_frame, width=30)
        self.folder_entry.pack(pady=(0, 10))
        self.folder_entry.insert(0, self.ui_state["folder_name"])
        
        # Area selection frame
        area_frame = tk.Frame(main_frame)
        area_frame.pack(pady=10)
        
        # Select area button centered
        tk.Button(area_frame, text="Select Area", command=self.start_area_selection).pack()
        
        # Coordinates display and edit frame
        coord_frame = tk.Frame(main_frame)
        coord_frame.pack(pady=10)
        
        # Coordinate entry fields
        self.coord_vars = []
        labels = ['X:', 'Y:', 'Width:', 'Height:']
        for i, label in enumerate(labels):
            tk.Label(coord_frame, text=label).grid(row=0, column=i*2, padx=2)
            var = tk.StringVar()
            entry = tk.Entry(coord_frame, textvariable=var, width=5)
            entry.grid(row=0, column=i*2+1, padx=2)
            self.coord_vars.append(var)
            if i < len(self.ui_state["coordinates"]):
                var.set(self.ui_state["coordinates"][i])
        
        # Update coordinates button
        tk.Button(coord_frame, text="Update", command=self.update_coords).grid(row=0, column=8, padx=5)
        
        # Take screenshot button centered
        tk.Button(main_frame, text="Take Screenshot", command=self.take_screenshot).pack(pady=10)
        
        # Preview frame
        self.preview_frame = tk.Frame(main_frame, borderwidth=2, relief="groove")
        self.preview_frame.pack(pady=10, fill=tk.BOTH, expand=True)
        self.preview_label = tk.Label(self.preview_frame, text="Screenshot preview will appear here")
        self.preview_label.pack(pady=20)
        
        # Settings button centered
        tk.Button(main_frame, text="Settings", command=self.show_settings).pack(pady=10)
        
        # Notification area (persistent)
        self.notification_frame = tk.Frame(self.root, height=30)
        self.notification_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.notification_label = tk.Label(self.notification_frame, text="", height=1)
        self.notification_label.pack(fill=tk.X)
        
        # Restore window geometry
        if self.settings.get("window_geometry"):
            self.root.geometry(self.settings["window_geometry"])

    def update_preview(self, image):
        """Update the preview area with the taken screenshot"""
        for widget in self.preview_frame.winfo_children():
            widget.destroy()
        
        max_width = 380
        max_height = 300
        
        img_width, img_height = image.size
        if img_width > max_width or img_height > max_height:
            ratio = min(max_width/img_width, max_height/img_height)
            new_size = (int(img_width * ratio), int(img_height * ratio))
            image = image.resize(new_size, ImageTk.Image.Resampling.LANCZOS)
        
        self.last_screenshot = ImageTk.PhotoImage(image)
        self.preview_label = tk.Label(self.preview_frame, image=self.last_screenshot)
        self.preview_label.pack(pady=10)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tk.Label(self.preview_frame, text=f"Screenshot taken at {timestamp}").pack()
    
    def show_notification(self, message, is_error=False):
        """Show a persistent notification in the bottom area"""
        self.notification_label.config(
            text=message,
            bg='red' if is_error else 'lightgreen',
            fg='white' if is_error else 'black',
            padx=10,
            pady=5,
            relief=tk.RAISED,
            borderwidth=1
        )
    
    def show_settings(self):
        """Show the settings menu"""
        self.save_ui_state()
        
        for widget in self.root.winfo_children():
            widget.destroy()
        
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(expand=True, fill=tk.BOTH)
        
        tk.Label(main_frame, text="Master Folder Path:").pack(pady=(10, 0))
        
        folder_frame = tk.Frame(main_frame)
        folder_frame.pack(pady=(0, 10), fill=tk.X)
        
        self.master_entry = tk.Entry(folder_frame, width=40)
        self.master_entry.insert(0, self.settings["master_folder"])
        self.master_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        
        tk.Button(folder_frame, text="Browse", command=self.browse_folder).pack(side=tk.LEFT, padx=5)
        
        tk.Button(main_frame, text="Back to Main Menu", command=self.return_to_main).pack(pady=20)
        
        # Notification area in settings too
        self.notification_frame = tk.Frame(self.root, height=30)
        self.notification_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.notification_label = tk.Label(self.notification_frame, text="", height=1)
        self.notification_label.pack(fill=tk.X)
    
    def return_to_main(self):
        """Return to main menu from settings"""
        self.settings["master_folder"] = self.master_entry.get()
        self.save_settings()
        self.create_main_gui()
        self.restore_ui_state()
    
    def browse_folder(self):
        """Open folder browser dialog"""
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.master_entry.delete(0, tk.END)
            self.master_entry.insert(0, folder_selected)
            self.show_notification(f"Master folder will be saved when you return to main menu")
    
    def start_area_selection(self):
        """Start the area selection process"""
        self.save_ui_state()
        self.root.withdraw()
        self.selection_active = True
        
        # Create selection window that spans all monitors
        self.selector = tk.Toplevel()
        self.selector.overrideredirect(True)  # Remove window decorations
        self.selector.attributes('-alpha', 0.3)
        self.selector.configure(bg='black')
        
        # Position and size to cover all monitors
        self.selector.geometry(f"{self.virtual_screen[2]}x{self.virtual_screen[3]}+{self.virtual_screen[0]}+{self.virtual_screen[1]}")
        
        # Create canvas for drawing
        self.canvas = tk.Canvas(self.selector, cursor="cross", bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Coordinate display label
        self.coord_label = tk.Label(self.selector, text="(0, 0)", bg='white', fg='black')
        self.coord_label.place(x=10, y=10)
        
        # Initialize selection variables
        self.start_x = None
        self.start_y = None
        self.rect = None
        self.crosshair_lines = []
        
        # Bind events
        self.canvas.bind("<Motion>", self.update_crosshair)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.selector.bind("<Escape>", self.cancel_selection)
    
    def update_crosshair(self, event):
        """Update crosshair lines and coordinate display"""
        # Remove old crosshair lines
        for line in self.crosshair_lines:
            self.canvas.delete(line)
        self.crosshair_lines = []
        
        # Draw vertical line
        self.crosshair_lines.append(
            self.canvas.create_line(event.x, 0, event.x, self.virtual_screen[3], 
                                   fill='red', dash=(2, 2))
        )
        
        # Draw horizontal line
        self.crosshair_lines.append(
            self.canvas.create_line(0, event.y, self.virtual_screen[2], event.y, 
                                   fill='red', dash=(2, 2))
        )
        
        # Update coordinate display
        self.coord_label.config(text=f"({event.x}, {event.y})")
    
    def on_press(self, event):
        """Handle mouse button press"""
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, 
            self.start_x, self.start_y, 
            outline='red', width=2
        )
    
    def on_drag(self, event):
        """Handle mouse drag"""
        if self.rect:
            self.canvas.coords(
                self.rect, 
                self.start_x, self.start_y, 
                event.x, event.y
            )
            self.update_crosshair(event)
    
    def on_release(self, event):
        """Handle mouse button release"""
        if self.rect:
            end_x = event.x
            end_y = event.y
            
            x1 = min(self.start_x, end_x)
            y1 = min(self.start_y, end_y)
            x2 = max(self.start_x, end_x)
            y2 = max(self.start_y, end_y)
            
            self.selected_area = (x1, y1, x2-x1, y2-y1)
            self.update_coord_display()
            self.show_notification("Area selected successfully")
            
            self.cleanup_selection()
    
    def cleanup_selection(self):
        """Clean up selection resources"""
        if self.selector:
            self.selector.destroy()
        self.root.deiconify()
        self.selection_active = False
    
    def cancel_selection(self, event=None):
        """Cancel the selection process"""
        self.show_notification("Area selection canceled", is_error=True)
        self.cleanup_selection()
    
    def update_coord_display(self):
        """Update the coordinate entry fields"""
        if self.selected_area:
            for i, val in enumerate(self.selected_area):
                self.coord_vars[i].set(str(val))
                self.ui_state["coordinates"][i] = str(val)
    
    def update_coords(self):
        """Update selected area from manual coordinate entry"""
        try:
            coords = [int(var.get()) for var in self.coord_vars]
            if len(coords) == 4:
                self.selected_area = tuple(coords)
                self.ui_state["coordinates"] = coords
                self.show_notification("Coordinates updated successfully")
            else:
                self.show_notification("Please enter all four values", is_error=True)
        except ValueError:
            self.show_notification("Please enter valid numbers", is_error=True)
    
    def take_screenshot(self):
        """Take a screenshot of the selected area and save it"""
        if not self.selected_area:
            self.show_notification("Please select an area first", is_error=True)
            return
            
        self.save_ui_state()
        
        try:
            self.root.withdraw()
            time.sleep(0.2)
            
            # Adjust coordinates for virtual screen
            adjusted_x = self.selected_area[0] + self.virtual_screen[0]
            adjusted_y = self.selected_area[1] + self.virtual_screen[1]
            
            screenshot = ImageGrab.grab(bbox=(
                adjusted_x,
                adjusted_y,
                adjusted_x + self.selected_area[2],
                adjusted_y + self.selected_area[3]
            ))
            
            full_path = os.path.join(self.settings["master_folder"], self.ui_state["folder_name"])
            os.makedirs(full_path, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(full_path, f"screenshot_{timestamp}.png")
            screenshot.save(filename)
            
            self.root.deiconify()
            self.update_preview(screenshot)
            self.show_notification(f"Screenshot saved to: {filename}")
            
        except Exception as e:
            self.show_notification(f"Error: {str(e)}", is_error=True)
            self.root.deiconify()

if __name__ == "__main__":
    root = tk.Tk()
    window_width = 400
    window_height = 500
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    x = (screen_width // 2) - (window_width // 2)
    y = (screen_height // 2) - (window_height // 2)
    root.geometry(f'{window_width}x{window_height}+{x}+{y}')
    
    app = ScreenshotTool(root)
    root.mainloop()