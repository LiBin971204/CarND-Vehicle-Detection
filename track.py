from collections import deque
import math
import moviepy
from moviepy.editor import VideoFileClip
import numpy as np
from scipy.ndimage.measurements import label

import search as sr
import train as tr

class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        
    def get_distance(self, other):
        return math.sqrt((self.x - other.x) ** 2 
                       + (self.y - other.y) ** 2)

def get_center(a, b):
    return Point((a.x + b.x) // 2, (a.y + b.y) // 2)
                       
class Box:
    def __init__(self, tuple):
        self.top_left = Point(tuple[0][0], tuple[0][1])
        self.bottom_right = Point(tuple[1][0], tuple[1][1])
        self.center = get_center(self.top_left, self.bottom_right)
        
    def get_area(self):
        width = self.bottom_right.x - self.top_left.x
        height = self.bottom_right.y - self.top_left.y
        return width * height
        
    def get_overlap_area(self, other):
        x_overlap = max(0, min(self.bottom_right.x, other.bottom_right.x) - max(self.top_left.x, other.top_left.x))
        y_overlap = max(0, min(self.bottom_right.y, other.bottom_right.y) - max(self.top_left.y, other.top_left.y))
        return x_overlap * y_overlap
        
    def as_tuple(self):
        return ((self.top_left.x, self.top_left.y), (self.bottom_right.x, self.bottom_right.y))
        
    def get_center_distance(self, other):
        return self.center.get_distance(other.center)

class Vehicle:
    def __init__(self, box, window_size):
        self.box = box
        self.window_size = window_size
        
        self.boxes = deque()
        self.boxes.appendleft(box)
        
        self.frames_since_detected = 0
        
        self.frames_detected = 1
        
    def check_ownership(self, boxes):
        claimed = []
        for box in boxes:
            claimed.append(self.check_ownership_single(box))
        for c in claimed:
            if c:
                self.frames_since_detected = 0
                self.frames_detected += 1
                return claimed
        self.frames_since_detected += 1
        if self.frames_since_detected > self.window_size:
            self.box = None
            self.frames_detected = 0
        return claimed
        
    def check_ownership_single(self, other):
        if (self.box.get_overlap_area(other) > 0 or self.box.get_center_distance(other) < 20):
            self.boxes.appendleft(other)
            if len(self.boxes) > self.window_size:
                self.boxes.pop()
            self.update_box()
            return True
        else:
            return False
            
    def update_box(self):
        avg_box = [[0, 0], [0, 0]]
        for box in self.boxes:
            b = box.as_tuple()
            for i in [0, 1]:
                for j in [0, 1]:
                    avg_box[i][j] += b[i][j]
        for i in [0, 1]:
            for j in [0, 1]:
                avg_box[i][j] //= len(self.boxes)
            
        self.box = Box(((avg_box[0][0], avg_box[0][1]), (avg_box[1][0], avg_box[1][1])))

class Frame:
    def __init__(self, heatmap, label_boxes):
        self.heatmap = heatmap
        self.label_boxes = label_boxes
        
class Tracker:
    def __init__(self, searcher, search_params, heatmap_window_size, heatmap_threshold_per_frame, vehicle_window_size):
        self.searcher = searcher
        self.search_params = search_params
        self.heatmap_window_size = heatmap_window_size
        self.heatmap_threshold_per_frame = heatmap_threshold_per_frame
        self.vehicle_window_size = vehicle_window_size
        self.frames = deque()
        self.smoothed_frames = deque()
        self.vehicles = []
        self.heatmap_boxes_count = 0
        
    def track(self, img):
        self.add_frame(searcher.search(img, 'full', self.search_params))
        
        heatmap, boxes = self.smooth_heatmaps()
        self.add_smoothed_frame(heatmap, boxes)
        
        self.check_box_change(boxes)
        
        self.update_vehicles(boxes)
        self.remove_vehicles()
        
        return self.draw_vehicle_boxes(img)
        
    def check_box_change(self, boxes):
        if len(boxes) != self.heatmap_boxes_count:
            i = 0
            for f in self.smoothed_frames:
                if i >= 3:
                    # Box change is genuine, so reset vehicles.
                    break
                if len(boxes) != len(f.label_boxes):
                    # Do not reset vehicles because count of heatmap boxes has not stabilized.
                    return
                i+=1
            self.reset_vehicles()
            self.heatmap_boxes_count = len(boxes)
        
    def reset_vehicles(self):
        self.vehicles[:] = []
        
    def update_vehicles(self, box_tuples):
        boxes = []
        for tuple in box_tuples:
            boxes.append(Box(tuple))
    
        claimed = [False] * len(boxes)
        
        for vehicle in self.vehicles:
            box_claimed = vehicle.check_ownership(boxes)
            for i in range(len(claimed)):
                claimed[i] = claimed[i] or box_claimed[i]
        
        for claimed, box in zip(claimed, boxes):
            if not claimed:
                self.vehicles.append(Vehicle(box, self.vehicle_window_size))
    
    def remove_vehicles(self):
        removal_list = []
        for vehicle in self.vehicles:
            if vehicle.box is None:
                removal_list.append(vehicle)
        for vehicle in removal_list:
            self.vehicles.remove(vehicle)
        
    def draw_vehicle_boxes(self, img):
        boxes = []
        for vehicle in self.vehicles:
            if vehicle.box is not None:
                boxes.append(vehicle.box.as_tuple())
                
        return sr.draw_boxes(img, boxes)
        
    def add_frame(self, search_result):
        heatmap = search_result[0]
        label_boxes = search_result[1]
        self.frames.appendleft(Frame(heatmap, label_boxes))
        
        if len(self.frames) > self.heatmap_window_size:
            discard_frame = self.frames.pop()

    def add_smoothed_frame(self, heatmap, boxes):
        self.smoothed_frames.appendleft(Frame(heatmap, boxes))
        
        if len(self.smoothed_frames) > self.heatmap_window_size:
            discard_frame = self.smoothed_frames.pop()
            
    def smooth_heatmaps(self):
        heatmaps = []
        for f in self.frames:
            heatmaps.append(f.heatmap)
        heatmap = np.sum(heatmaps, axis = 0)
        heatmap = self.boost_heatmap(heatmap)
        heatmap = sr.apply_threshold(heatmap, int(self.heatmap_threshold_per_frame * len(self.frames)))
        labels = label(heatmap)
        boxes = sr.convert_to_bboxes(labels)
        return heatmap, boxes
        
    def boost_heatmap(self, heatmap):
        for vehicle in self.vehicles:
            if vehicle.frames_detected > 24:
                b = vehicle.box
                heatmap[b.top_left.y : b.bottom_right.y, b.top_left.x : b.bottom_right.x] *= 3
        return heatmap
            
def process_img(img):
    global tracker
    return tracker.track(img)
    
def process_video(input_fname, output_fname):
    input_clip = VideoFileClip(input_fname)
    output_clip = input_clip.fl_image(process_img)
    output_clip.write_videofile(output_fname, audio=False)
        
if __name__ == '__main__':
    import os
    folder = 'output_video'
    if not os.path.isdir(folder):
        os.makedirs(folder)
        
    # model = 'trained_models/HSV-ss(16, 16)-hb16-o9-p8-c2-hcALL-sf1-hist1-hog1-acc99.49.p'
    # model = 'trained_models/HLS-ss(16, 16)-hb16-o9-p10-c2-hcALL-sf1-hist1-hog1-acc99.32.p'
    # model = 'trained_models/YCrCb-ss(16, 16)-hb16-o9-p8-c2-hcALL-sf1-hist1-hog1-acc99.21.p'
    model = 'trained_models/YCrCb-ss(16, 16)-hb16-o9-p8-c2-hcALL-sf1-hist1-hog1-acc99.72.p'
    (fp, clf, X_scaler) = tr.load_classifier(model)
    
    searcher = sr.Searcher(fp, clf, X_scaler)    
    
    sp = sr.SearchParams.get_defaults()
    
    for heatmap_window_size in [10]:
        for threshold in [1.7]:
            for vehicle_window_size in [7]:
                tracker = Tracker(searcher, sp, 
                                    heatmap_window_size, 
                                    heatmap_threshold_per_frame = threshold, 
                                    vehicle_window_size = vehicle_window_size)
                process_video('test_video.mp4', 'output_video/test_video_boost_heat_th{}_hw{}_vw{}.mp4'.format(threshold, heatmap_window_size, vehicle_window_size))
    
    for heatmap_window_size in [10, 12]:
        for threshold in [1.7]:
            for vehicle_window_size in [7]:
                print('Threshold: ', threshold, ', Heatmap Window Size: ', heatmap_window_size, ', Vehicle Window Size: ', vehicle_window_size)
                tracker = Tracker(searcher, sp, 
                                    heatmap_window_size, 
                                    heatmap_threshold_per_frame = threshold, 
                                    vehicle_window_size = vehicle_window_size)
                process_video('project_video.mp4', 'output_video/project_video_boost_heat_th{}_hw{}_vw{}.mp4'.format(threshold, heatmap_window_size, vehicle_window_size))
